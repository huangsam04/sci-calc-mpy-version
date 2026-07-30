# Production mpremote release adapter.
# Drives a real device through the A/B slot transaction: selector and
# boot-log records remain fixed, dual files validated by the shared host/device
# codecs. File staging lands in /sd/.staging and is renamed into the candidate
# slot only after device-side SHA-256 verification passes.
import binascii
import hashlib
from dataclasses import dataclass
from pathlib import Path

import bootenv
import bootlog
import bootsel
from tools.release_plan import (
    MPY_ABI_TAG,
    SOURCE_ABI_TAG,
    _validated_manifest,
    cleanup_candidates,
)
from tools.release_protocol import (
    ColdBootObservation,
    HashReceipt,
    OWNER_MARKER_NAME,
    ReleaseSmokeResult,
    SelectionTicket,
    SlotRef,
    owner_marker_payload,
    run_guarded_session,
)

_SEL0, _SEL1 = bootenv.SELECTOR_PATHS
_LOG0, _LOG1 = bootenv.BOOTLOG_PATHS
_STAGING_ROOT = "/sd/.staging"
# Codec maxima bound the only internal records read into host memory. Keeping
# codec imports off the running calculator avoids recompiling trusted root
# sources in its constrained heap during release confirmation.
_SELECTOR_RECORD_MAX_BYTES = 921
_BOOTLOG_RECORD_MAX_BYTES = 351

# Selector controls can run before the resident application exists. Explicitly
# restore only the trusted internal root before importing the shared codecs.
_TRUSTED_BOOT_IMPORT = (
    "import sys\n"
    "sys.path.insert(0,'/') if '/' not in sys.path else None\n")
SELECTOR_READ_CODE = _TRUSTED_BOOT_IMPORT + (
    "import bootsel,binascii\n"
    "d=bootsel.SelectorStore('" + _SEL0 + "','" + _SEL1 + "').read()\n"
    "print(binascii.hexlify(bootsel.pack_record(d)).decode()"
    " if d else 'NONE')")
SELECTOR_READ_MAX_BYTES = 2048
SELECTOR_WRITE_CODE = _TRUSTED_BOOT_IMPORT + (
    "import bootsel,binascii\n"
    "fields={fields}\n"
    "def _ref(r):\n"
    "    return None if r is None else bootsel.SlotEntry(\n"
    "        r[0],r[1],binascii.unhexlify(r[2]))\n"
    "d=bootsel.SelectorData(0,_ref(fields[0]),_ref(fields[1]),\n"
    "    fields[2],fields[3],tuple(_ref(r) for r in fields[4]),fields[5])\n"
    "s=bootsel.SelectorStore('" + _SEL0 + "','" + _SEL1 + "')\n"
    "stored=s.write(d)\n"
    "print(binascii.hexlify(bootsel.pack_record(stored)).decode())")
SELECTOR_WRITE_MAX_BYTES = 2048
BOOTLOG_READ_CODE = _TRUSTED_BOOT_IMPORT + (
    "import bootlog,binascii\n"
    "e=bootlog.BootLogStore('" + _LOG0 + "','" + _LOG1 + "').read()\n"
    "print(binascii.hexlify(bootlog.pack_record(e)).decode()"
    " if e else 'NONE')")
BOOTLOG_READ_MAX_BYTES = 768

HASH_PATHS_CODE = (
    "import gc,hashlib,binascii\n"
    "matched=0\n"
    "missing=0\n"
    "fault=0\n"
    "try:\n"
    "    buf=bytearray(512)\n"
    "    view=memoryview(buf)\n"
    "    for index,pair in enumerate({pairs}):\n"
    "        path,sha=pair\n"
    "        try:\n"
    "            stream=None\n"
    "            primary=None\n"
    "            try:\n"
    "                h=hashlib.sha256()\n"
    "                stream=open(path,'rb')\n"
    "                while True:\n"
    "                    n=stream.readinto(buf)\n"
    "                    if not n: break\n"
    "                    if n==512: h.update(buf)\n"
    "                    elif n<512: h.update(view[:n])\n"
    "                    else: raise ValueError('invalid readinto size')\n"
    "                if binascii.hexlify(h.digest()).decode()==sha:\n"
    "                    matched|=1<<index\n"
    "            except BaseException as error:\n"
    "                primary=error\n"
    "                raise\n"
    "            finally:\n"
    "                if stream is not None:\n"
    "                    try:\n"
    "                        stream.close()\n"
    "                    except Exception:\n"
    "                        if primary is None: raise\n"
    "        except OSError as error:\n"
    "            code=(error.args[0] if error.args else None)\n"
    "            if code==2: missing|=1<<index\n"
    "            else: raise\n"
    "except MemoryError:\n"
    "    raise\n"
    "except Exception:\n"
    "    fault=1\n"
    "    matched=0\n"
    "    missing=0\n"
    "receipt=('E' if fault else 'H')+'%03x%03x'%(matched,missing)\n"
    "view=None\n"
    "buf=None\n"
    "h=None\n"
    "stream=None\n"
    "gc.collect()\n"
    "print(receipt)")

HASH_RECEIPT_MAX_PAIRS = 10
# A query path travels inside the raw-REPL literal, so it carries its own
# host-side cap independent of the on-device slot path budget.
HASH_PATH_MAX_CHARS = 128
# The fixed HASH_PATHS_CODE source is under 1 KiB; ten maximal pairs cost
# about 10*(HASH_PATH_MAX_CHARS + 64 digest chars + ~10 repr punctuation)
# ~= 2 KiB of literal, so 3584 covers the largest legal query with slack
# while refusing escape-inflated or otherwise runaway queries.
HASH_QUERY_MAX_BYTES = 3584
_HASH_RECEIPT_TEXT_BYTES = 7
_HASH_RECEIPT_MAX_OUTPUT_BYTES = 16
_LOWER_HEX = frozenset("0123456789abcdef")

_VERIFY_MANIFEST_MAX_BYTES = 65536
_VERIFY_MANIFEST_CHUNK_BYTES = 256
_VERIFY_MANIFEST_MAX_READS = 320
_VERIFY_MANIFEST_MAX_RECORDS = 256
_VERIFY_RECORD_MAX_BYTES = 1024
_VERIFY_PATH_MAX_CHARS = 255
_VERIFY_FILE_HASH_CHUNK_BYTES = 512


def _hash_pairs_literal(pairs):
    """Validate and encode the fixed-size target hash query for raw REPL."""
    normalized = []
    for pair in pairs:
        if len(normalized) >= HASH_RECEIPT_MAX_PAIRS:
            raise ValueError("hash receipt requires 1..10 paths")
        if type(pair) is not tuple or len(pair) != 2:
            raise ValueError("invalid hash receipt pair")
        path, digest = pair
        if (type(path) is not str or type(digest) is not str
                or not path.startswith("/") or path == "/"
                or len(path) > HASH_PATH_MAX_CHARS
                or "\\" in path or "\x00" in path
                or any(ord(char) < 32 or ord(char) > 127 for char in path)
                or any(part in ("", ".", "..")
                       for part in path[1:].split("/"))
                or len(digest) != 64
                or any(char not in _LOWER_HEX for char in digest)):
            raise ValueError("invalid hash receipt pair")
        normalized.append((path, digest))
    if not normalized:
        raise ValueError("hash receipt requires 1..10 paths")
    literal = repr(tuple(normalized))
    query = HASH_PATHS_CODE.format(pairs=literal)
    if len(query.encode("utf-8")) > HASH_QUERY_MAX_BYTES:
        raise ValueError("hash receipt query is too large")
    return literal, len(normalized)


def _parse_hash_receipt(text, pair_count):
    # A bounded transport enforces this at the byte boundary, but adapters
    # are still a public seam.  Never allocate a stripped copy of an
    # arbitrary adapter response before enforcing the fixed wire cap.
    if (type(text) is not str
            or len(text) > _HASH_RECEIPT_MAX_OUTPUT_BYTES):
        raise ValueError("invalid device hash receipt")
    for char in text:
        if ord(char) > 127:
            raise ValueError("invalid device hash receipt")
    text = text.strip()
    if (len(text) != _HASH_RECEIPT_TEXT_BYTES
            or text[0] not in ("H", "E")
            or any(char not in _LOWER_HEX for char in text[1:])):
        raise ValueError("invalid device hash receipt")
    if text[0] == "E":
        if text[1:] != "000000":
            raise ValueError("invalid device hash fault receipt")
        return HashReceipt(0, 0, True)

    matched_mask = int(text[1:4], 16)
    missing_mask = int(text[4:7], 16)
    valid_mask = (1 << pair_count) - 1
    if (matched_mask & missing_mask
            or matched_mask & ~valid_mask
            or missing_mask & ~valid_mask):
        raise ValueError("invalid device hash receipt masks")
    return HashReceipt(matched_mask, missing_mask, False)


def stream_hash_receipt(device, pairs):
    """Return bounded streaming SHA evidence for at most ten target paths."""
    literal, pair_count = _hash_pairs_literal(pairs)
    execute_limited = getattr(device, "exec_limited", None)
    if not callable(execute_limited):
        raise ValueError("device must provide bounded exec")
    text = execute_limited(
        HASH_PATHS_CODE,
        _HASH_RECEIPT_MAX_OUTPUT_BYTES,
        pairs=literal,
    )
    return _parse_hash_receipt(text, pair_count)

# This source runs directly in the target raw REPL.  It never materializes a
# full manifest or its JSON tree: the canonical envelope is scanned as it is
# hashed, while json.loads sees one fixed-size asset record at a time.
VERIFY_SLOT_CODE = (
    "import hashlib,binascii,json,os\n"
    "root='{slot_root}'\n"
    "max_manifest_bytes=" + str(_VERIFY_MANIFEST_MAX_BYTES) + "\n"
    "manifest_chunk_bytes=" + str(_VERIFY_MANIFEST_CHUNK_BYTES) + "\n"
    "max_manifest_reads=" + str(_VERIFY_MANIFEST_MAX_READS) + "\n"
    "max_manifest_records=" + str(_VERIFY_MANIFEST_MAX_RECORDS) + "\n"
    "max_record_bytes=" + str(_VERIFY_RECORD_MAX_BYTES) + "\n"
    "max_path_chars=" + str(_VERIFY_PATH_MAX_CHARS) + "\n"
    "file_chunk_bytes=" + str(_VERIFY_FILE_HASH_CHUNK_BYTES) + "\n"
    "record_fields=('format','key','path','role','sha256','size','zone')\n"
    "def _lower_hex(value):\n"
    "    if type(value) is not str or len(value)!=64: return False\n"
    "    for char in value:\n"
    "        if not ('0'<=char<='9' or 'a'<=char<='f'): return False\n"
    "    return True\n"
    "def _safe_path(path):\n"
    "    if (type(path) is not str or not path or len(path)>max_path_chars\n"
    "            or path[0]=='/' or path[-1]=='/'): return False\n"
    "    start=0\n"
    "    length=len(path)\n"
    "    for index in range(length):\n"
    "        char=path[index]\n"
    "        if ord(char)<32 or ord(char)>126: return False\n"
    "        if char=='\\\\' or char==':': return False\n"
    "        if char=='/':\n"
    "            if index==start: return False\n"
    "            if index==start+1 and path[start]=='.': return False\n"
    "            if (index==start+2 and path[start]=='.'\n"
    "                    and path[start+1]=='.'): return False\n"
    "            start=index+1\n"
    "    if start==length: return False\n"
    "    if length==start+1 and path[start]=='.': return False\n"
    "    if (length==start+2 and path[start]=='.'\n"
    "            and path[start+1]=='.'): return False\n"
    "    return True\n"
    "def _record_error(text,seeds,file_chunk,file_view):\n"
    "    try:\n"
    "        rec=json.loads(text)\n"
    "    except MemoryError:\n"
    "        raise\n"
    "    except Exception:\n"
    "        return 'MANIFEST'\n"
    "    if type(rec) is not dict or len(rec)!=7: return 'MANIFEST'\n"
    "    for field in record_fields:\n"
    "        if field not in rec: return 'MANIFEST'\n"
    "    if (type(rec['format']) is not str or type(rec['key']) is not str\n"
    "            or type(rec['path']) is not str\n"
    "            or type(rec['role']) is not str\n"
    "            or type(rec['zone']) is not str\n"
    "            or type(rec['size']) is not int): return 'MANIFEST'\n"
    "    path=rec['path']\n"
    "    role=rec['role']\n"
    "    zone=rec['zone']\n"
    "    size=rec['size']\n"
    "    sha=rec['sha256']\n"
    "    if size<0 or not _safe_path(path) or not _lower_hex(sha):\n"
    "        return 'MANIFEST'\n"
    "    if seeds:\n"
    "        if role!='seed_if_absent' or rec['format']!='seed': return 'MANIFEST'\n"
    "    elif (role!='bootstrap_fixed' and role!='managed_release') or rec['format'] not in ('source','mpy','font'):\n"
    "        return 'MANIFEST'\n"
    "    if zone!='internal' and zone!='sd': return 'MANIFEST'\n"
    "    if role!='managed_release' or zone!='sd': return ''\n"
    "    stream=None\n"
    "    result=''\n"
    "    fatal=None\n"
    "    try:\n"
    "        if os.stat(root+'/'+path)[6]!=size:\n"
    "            result='HASH '+path\n"
    "        else:\n"
    "            expected=binascii.unhexlify(sha)\n"
    "            h=hashlib.sha256()\n"
    "            stream=open(root+'/'+path,'rb')\n"
    "            remaining=size\n"
    "            reads=0\n"
    "            max_reads=(size+file_chunk_bytes-1)//file_chunk_bytes+1\n"
    "            while remaining:\n"
    "                count=stream.readinto(file_chunk)\n"
    "                reads+=1\n"
    "                if (count is None or isinstance(count,bool)\n"
    "                        or not isinstance(count,int) or count<=0\n"
    "                        or count>file_chunk_bytes or count>remaining\n"
    "                        or reads>max_reads):\n"
    "                    result='HASH '+path\n"
    "                    break\n"
    "                h.update(file_view[:count])\n"
    "                remaining-=count\n"
    "            if not result and h.digest()!=expected:\n"
    "                result='HASH '+path\n"
    "    except MemoryError as error:\n"
    "        fatal=error\n"
    "    except OSError:\n"
    "        result='MISSING '+path\n"
    "    except Exception:\n"
    "        result='MANIFEST'\n"
    "    try:\n"
    "        if stream is not None: stream.close()\n"
    "    except MemoryError as error:\n"
    "        if fatal is None: fatal=error\n"
    "    except Exception:\n"
    "        if fatal is None and not result: result='MANIFEST'\n"
    "    if fatal is not None: raise fatal\n"
    "    return result\n"
    "S_LITERAL=0\n"
    "S_STRING_START=1\n"
    "S_STRING_BODY=2\n"
    "S_ASSETS_ITEM=3\n"
    "S_ASSETS_DELIMITER=4\n"
    "S_SEEDS_ITEM=5\n"
    "S_SEEDS_DELIMITER=6\n"
    "S_RECORD=7\n"
    "S_DONE=8\n"
    "bad=''\n"
    "manifest_stream=None\n"
    "fatal=None\n"
    "state=-1\n"
    "expected_manifest=None\n"
    "manifest_hash=None\n"
    "try:\n"
    "    expected_manifest=binascii.unhexlify('{manifest_sha256}')\n"
    "    if len(expected_manifest)!=32:\n"
    "        bad='MANIFEST'\n"
    "    manifest_hash=hashlib.sha256()\n"
    "    manifest_chunk=bytearray(manifest_chunk_bytes)\n"
    "    manifest_view=memoryview(manifest_chunk)\n"
    "    file_chunk=bytearray(file_chunk_bytes)\n"
    "    file_view=memoryview(file_chunk)\n"
    "    record=bytearray(max_record_bytes)\n"
    "    field=bytearray(96)\n"
    "    state=S_LITERAL\n"
    "    literal=b'{{\"abi_tag\":'\n"
    "    literal_at=0\n"
    "    after_literal=S_STRING_START\n"
    "    pending_string=0\n"
    "    string_kind=0\n"
    "    string_bytes=0\n"
    "    string_length=0\n"
    "    string_plain=True\n"
    "    string_escaped=False\n"
    "    string_unicode=0\n"
    "    record_length=0\n"
    "    record_string=False\n"
    "    record_escaped=False\n"
    "    record_unicode=0\n"
    "    record_seeds=False\n"
    "    record_count=0\n"
    "    manifest_bytes=0\n"
    "    manifest_reads=0\n"
    "    manifest_stream=open(root+'/{manifest_name}','rb')\n"
    "    while not bad:\n"
    "        count=manifest_stream.readinto(manifest_chunk)\n"
    "        if (count is None or isinstance(count,bool)\n"
    "                or not isinstance(count,int) or count<0\n"
    "                or count>manifest_chunk_bytes):\n"
    "            bad='MANIFEST'\n"
    "            break\n"
    "        if count==0: break\n"
    "        manifest_reads+=1\n"
    "        manifest_bytes+=count\n"
    "        if (manifest_reads>max_manifest_reads\n"
    "                or manifest_bytes>max_manifest_bytes):\n"
    "            bad='MANIFEST'\n"
    "            break\n"
    "        manifest_hash.update(manifest_view[:count])\n"
    "        for index in range(count):\n"
    "            value=manifest_view[index]\n"
    "            if value>127:\n"
    "                bad='MANIFEST'\n"
    "                break\n"
    "            if state==S_DONE:\n"
    "                bad='MANIFEST'\n"
    "                break\n"
    "            if state==S_LITERAL:\n"
    "                if value!=literal[literal_at]:\n"
    "                    bad='MANIFEST'\n"
    "                    break\n"
    "                literal_at+=1\n"
    "                if literal_at==len(literal):\n"
    "                    literal=None\n"
    "                    literal_at=0\n"
    "                    state=after_literal\n"
    "                    if state==S_STRING_START:\n"
    "                        string_kind=pending_string\n"
    "                continue\n"
    "            if state==S_STRING_START:\n"
    "                if value!=34:\n"
    "                    bad='MANIFEST'\n"
    "                    break\n"
    "                string_bytes=0\n"
    "                string_length=0\n"
    "                string_plain=True\n"
    "                string_escaped=False\n"
    "                string_unicode=0\n"
    "                state=S_STRING_BODY\n"
    "                continue\n"
    "            if state==S_STRING_BODY:\n"
    "                if string_unicode:\n"
    "                    if not (48<=value<=57 or 65<=value<=70\n"
    "                            or 97<=value<=102):\n"
    "                        bad='MANIFEST'\n"
    "                        break\n"
    "                    string_bytes+=1\n"
    "                    string_unicode-=1\n"
    "                    if string_unicode==0: string_escaped=False\n"
    "                elif string_escaped:\n"
    "                    string_bytes+=1\n"
    "                    if value==117:\n"
    "                        string_unicode=4\n"
    "                    elif value in (34,47,92,98,102,110,114,116):\n"
    "                        string_escaped=False\n"
    "                    else:\n"
    "                        bad='MANIFEST'\n"
    "                        break\n"
    "                elif value==34:\n"
    "                    if string_kind==2:\n"
    "                        if (not string_plain or string_length not in (3,6)\n"
    "                                or bytes(field[:string_length])"
    " not in (b'mpy',b'source')):\n"
    "                            bad='MANIFEST'\n"
    "                            break\n"
    "                        literal=b',\"product\":'\n"
    "                        after_literal=S_STRING_START\n"
    "                        pending_string=3\n"
    "                    elif string_kind==3:\n"
    "                        if (not string_plain or string_length!=8\n"
    "                                or bytes(field[:string_length])!=b'sci-calc'):\n"
    "                            bad='MANIFEST'\n"
    "                            break\n"
    "                        literal=b',\"release_id\":'\n"
    "                        after_literal=S_STRING_START\n"
    "                        pending_string=4\n"
    "                    elif string_kind==4:\n"
    "                        release_ok=string_plain and string_length==64\n"
    "                        if release_ok:\n"
    "                            for digit_at in range(string_length):\n"
    "                                digit=field[digit_at]\n"
    "                                if not (48<=digit<=57 or 97<=digit<=102):\n"
    "                                    release_ok=False\n"
    "                                    break\n"
    "                        if not release_ok:\n"
    "                            bad='MANIFEST'\n"
    "                            break\n"
    "                        literal=b',\"schema\":1,\"seeds\":['\n"
    "                        after_literal=S_SEEDS_ITEM\n"
    "                    elif string_kind==0:\n"
    "                        literal=b',\"app_version\":'\n"
    "                        after_literal=S_STRING_START\n"
    "                        pending_string=1\n"
    "                    elif string_kind==1:\n"
    "                        literal=b',\"assets\":['\n"
    "                        after_literal=S_ASSETS_ITEM\n"
    "                    else:\n"
    "                        bad='MANIFEST'\n"
    "                        break\n"
    "                    literal_at=0\n"
    "                    state=S_LITERAL\n"
    "                    continue\n"
    "                elif value<32:\n"
    "                    bad='MANIFEST'\n"
    "                    break\n"
    "                else:\n"
    "                    string_bytes+=1\n"
    "                    if value==92:\n"
    "                        string_plain=False\n"
    "                        string_escaped=True\n"
    "                    elif string_kind>=2:\n"
    "                        if string_length>=len(field):\n"
    "                            bad='MANIFEST'\n"
    "                            break\n"
    "                        field[string_length]=value\n"
    "                        string_length+=1\n"
    "                if string_bytes>len(field):\n"
    "                    bad='MANIFEST'\n"
    "                    break\n"
    "                continue\n"
    "            if state==S_ASSETS_ITEM or state==S_SEEDS_ITEM:\n"
    "                if value==93:\n"
    "                    if state==S_ASSETS_ITEM:\n"
    "                        literal=b',\"mode\":'\n"
    "                        after_literal=S_STRING_START\n"
    "                        pending_string=2\n"
    "                    else:\n"
    "                        literal=b'}}'\n"
    "                        after_literal=S_DONE\n"
    "                    literal_at=0\n"
    "                    state=S_LITERAL\n"
    "                    continue\n"
    "                if value!=123 or record_count>=max_manifest_records:\n"
    "                    bad='MANIFEST'\n"
    "                    break\n"
    "                record_count+=1\n"
    "                record[0]=123\n"
    "                record_length=1\n"
    "                record_string=False\n"
    "                record_escaped=False\n"
    "                record_unicode=0\n"
    "                record_seeds=state==S_SEEDS_ITEM\n"
    "                state=S_RECORD\n"
    "                continue\n"
    "            if state==S_ASSETS_DELIMITER or state==S_SEEDS_DELIMITER:\n"
    "                if value==44:\n"
    "                    state=(S_SEEDS_ITEM if state==S_SEEDS_DELIMITER\n"
    "                           else S_ASSETS_ITEM)\n"
    "                    continue\n"
    "                if value==93:\n"
    "                    if state==S_ASSETS_DELIMITER:\n"
    "                        literal=b',\"mode\":'\n"
    "                        after_literal=S_STRING_START\n"
    "                        pending_string=2\n"
    "                    else:\n"
    "                        literal=b'}}'\n"
    "                        after_literal=S_DONE\n"
    "                    literal_at=0\n"
    "                    state=S_LITERAL\n"
    "                    continue\n"
    "                bad='MANIFEST'\n"
    "                break\n"
    "            if state==S_RECORD:\n"
    "                if record_length>=max_record_bytes:\n"
    "                    bad='MANIFEST'\n"
    "                    break\n"
    "                record[record_length]=value\n"
    "                record_length+=1\n"
    "                if record_unicode:\n"
    "                    if not (48<=value<=57 or 65<=value<=70\n"
    "                            or 97<=value<=102):\n"
    "                        bad='MANIFEST'\n"
    "                        break\n"
    "                    record_unicode-=1\n"
    "                    if record_unicode==0: record_escaped=False\n"
    "                elif record_escaped:\n"
    "                    if value==117:\n"
    "                        record_unicode=4\n"
    "                    elif value in (34,47,92,98,102,110,114,116):\n"
    "                        record_escaped=False\n"
    "                    else:\n"
    "                        bad='MANIFEST'\n"
    "                        break\n"
    "                elif record_string:\n"
    "                    if value==34:\n"
    "                        record_string=False\n"
    "                    elif value<32:\n"
    "                        bad='MANIFEST'\n"
    "                        break\n"
    "                    elif value==92:\n"
    "                        record_escaped=True\n"
    "                elif value==34:\n"
    "                    record_string=True\n"
    "                elif value==123 or value==91 or value==93:\n"
    "                    bad='MANIFEST'\n"
    "                    break\n"
    "                elif value==125:\n"
    "                    text=bytes(memoryview(record)[:record_length]).decode('ascii')\n"
    "                    bad=_record_error(text,record_seeds,file_chunk,file_view)\n"
    "                    del text\n"
    "                    if bad: break\n"
    "                    state=(S_SEEDS_DELIMITER if record_seeds\n"
    "                           else S_ASSETS_DELIMITER)\n"
    "                continue\n"
    "            bad='MANIFEST'\n"
    "            break\n"
    "except MemoryError as error:\n"
    "    fatal=error\n"
    "except OSError:\n"
    "    if not bad: bad='MISSING_MANIFEST'\n"
    "except Exception:\n"
    "    if not bad: bad='MANIFEST'\n"
    "try:\n"
    "    if manifest_stream is not None: manifest_stream.close()\n"
    "except MemoryError as error:\n"
    "    if fatal is None: fatal=error\n"
    "except Exception:\n"
    "    if fatal is None and not bad: bad='MANIFEST'\n"
    "if fatal is not None: raise fatal\n"
    "if not bad:\n"
    "    if state!=S_DONE or manifest_bytes<=0:\n"
    "        bad='MANIFEST'\n"
    "    elif manifest_hash.digest()!=expected_manifest:\n"
    "        bad='MANIFEST'\n"
    "print(bad if bad else 'OK')")

# The verifier accepts only printable ASCII paths, so the longest slot
# receipt is 'MISSING ' plus _VERIFY_PATH_MAX_CHARS ASCII bytes (263 total).
# 288 covers CR/LF framing and a small transport margin without relying on
# a Unicode character-count assumption.
VERIFY_SLOT_RECEIPT_MAX_BYTES = 288

# A complete MPY slot currently hashes dozens of SD assets before emitting
# its one receipt.  Keep that long-running control operation bounded without
# changing the normal 10-second raw-REPL timeout used by every other command.
VERIFY_SLOT_TIMEOUT_S = 60

# This source runs directly in the target raw REPL.  The slot manifest is
# streamed through SHA-256 with one fixed chunk buffer under hard read and
# byte caps, so the device never materializes the file; a MemoryError is
# re-raised untouched after the stream has been closed.
VALIDATE_MANIFEST_CODE = (
    "import hashlib,binascii\n"
    "max_manifest_bytes=" + str(_VERIFY_MANIFEST_MAX_BYTES) + "\n"
    "manifest_chunk_bytes=" + str(_VERIFY_MANIFEST_CHUNK_BYTES) + "\n"
    "max_manifest_reads=" + str(_VERIFY_MANIFEST_MAX_READS) + "\n"
    "bad=''\n"
    "stream=None\n"
    "fatal=None\n"
    "total=0\n"
    "try:\n"
    "    expected=binascii.unhexlify('{manifest_sha256}')\n"
    "    if len(expected)!=32:\n"
    "        bad='MANIFEST'\n"
    "    digest=hashlib.sha256()\n"
    "    chunk=bytearray(manifest_chunk_bytes)\n"
    "    view=memoryview(chunk)\n"
    "    reads=0\n"
    "    if not bad:\n"
    "        stream=open('{manifest_path}','rb')\n"
    "    while not bad:\n"
    "        count=stream.readinto(chunk)\n"
    "        if (count is None or isinstance(count,bool)\n"
    "                or not isinstance(count,int) or count<0\n"
    "                or count>manifest_chunk_bytes):\n"
    "            bad='MANIFEST'\n"
    "            break\n"
    "        if count==0: break\n"
    "        reads+=1\n"
    "        total+=count\n"
    "        if (reads>max_manifest_reads\n"
    "                or total>max_manifest_bytes):\n"
    "            bad='MANIFEST'\n"
    "            break\n"
    "        digest.update(view[:count])\n"
    "except MemoryError as error:\n"
    "    fatal=error\n"
    "except OSError:\n"
    "    if not bad: bad='MISSING'\n"
    "except Exception:\n"
    "    if not bad: bad='MANIFEST'\n"
    "try:\n"
    "    if stream is not None: stream.close()\n"
    "except MemoryError as error:\n"
    "    if fatal is None: fatal=error\n"
    "except Exception:\n"
    "    if fatal is None and not bad: bad='MANIFEST'\n"
    "if fatal is not None: raise fatal\n"
    "if not bad:\n"
    "    if total<=0: bad='MANIFEST'\n"
    "    elif digest.digest()!=expected: bad='HASH'\n"
    "print(bad if bad else 'OK')")

# Longest manifest receipt is 'MANIFEST' (8 chars) plus CR/LF.
VALIDATE_MANIFEST_RECEIPT_MAX_BYTES = 16

_OWNED_TREE_CHUNK_BYTES = 256
_OWNED_TREE_MAX_DIRECTORY_ENTRIES = _VERIFY_MANIFEST_MAX_RECORDS + 2
_OWNED_TREE_MAX_DEPTH = 64
_OWNED_TREE_CHUNK_RECEIPT_MAX_BYTES = 528
_OWNED_TREE_CONTROL_RECEIPT_MAX_BYTES = 16

# These raw-REPL primitives are intentionally private to _OwnedReleaseTrees.
# They receive only roots and leaves constructed by that module; no public
# transport method accepts a caller-provided deletion or rename path.
OWNED_TREE_RECEIPT_CODE = (
    "import os,hashlib,binascii\n"
    "root={root}\n"
    "manifest_name={manifest_name}\n"
    "manifest_sha256='{manifest_sha256}'\n"
    "owner_name={owner_name}\n"
    "owner_sha256='{owner_sha256}'\n"
    "chunk_bytes=" + str(_OWNED_TREE_CHUNK_BYTES) + "\n"
    "def _digest(path):\n"
    "    stream=None\n"
    "    primary=None\n"
    "    try:\n"
    "        digest=hashlib.sha256()\n"
    "        chunk=bytearray(chunk_bytes)\n"
    "        view=memoryview(chunk)\n"
    "        stream=open(path,'rb')\n"
    "        while True:\n"
    "            count=stream.readinto(chunk)\n"
    "            if count is None or count<0 or count>chunk_bytes:\n"
    "                raise ValueError('readinto')\n"
    "            if count==0: break\n"
    "            digest.update(chunk if count==chunk_bytes else view[:count])\n"
    "        return digest.digest()\n"
    "    except BaseException as error:\n"
    "        primary=error\n"
    "        raise\n"
    "    finally:\n"
    "        if stream is not None:\n"
    "            try:\n"
    "                stream.close()\n"
    "            except Exception:\n"
    "                if primary is None: raise\n"
    "try:\n"
    "    mode=os.stat(root)[0]\n"
    "except OSError as error:\n"
    "    code=error.args[0] if error.args else -1\n"
    "    print('M' if code==2 else 'F')\n"
    "else:\n"
    "    good=bool(mode&0x4000)\n"
    "    try:\n"
    "        expected_manifest=binascii.unhexlify(manifest_sha256)\n"
    "        expected_owner=binascii.unhexlify(owner_sha256)\n"
    "        if len(expected_manifest)!=32 or len(expected_owner)!=32:\n"
    "            good=False\n"
    "        if good and os.stat(root+'/'+manifest_name)[0]&0x4000:\n"
    "            good=False\n"
    "        if good and os.stat(root+'/'+owner_name)[0]&0x4000:\n"
    "            good=False\n"
    "        if good and _digest(root+'/'+manifest_name)!=expected_manifest:\n"
    "            good=False\n"
    "        if good and _digest(root+'/'+owner_name)!=expected_owner:\n"
    "            good=False\n"
    "    except MemoryError:\n"
    "        raise\n"
    "    except Exception:\n"
    "        good=False\n"
    "    print('O' if good else 'F')")

OWNED_TREE_FILE_RECEIPT_CODE = (
    "import hashlib,binascii\n"
    "path={path}\n"
    "expected_hex='{sha256}'\n"
    "chunk_bytes=" + str(_OWNED_TREE_CHUNK_BYTES) + "\n"
    "stream=None\n"
    "primary=None\n"
    "token='F'\n"
    "try:\n"
    "    expected=binascii.unhexlify(expected_hex)\n"
    "    if len(expected)!=32: raise ValueError('digest')\n"
    "    digest=hashlib.sha256()\n"
    "    chunk=bytearray(chunk_bytes)\n"
    "    view=memoryview(chunk)\n"
    "    stream=open(path,'rb')\n"
    "    while True:\n"
    "        count=stream.readinto(chunk)\n"
    "        if count is None or count<0 or count>chunk_bytes:\n"
    "            raise ValueError('readinto')\n"
    "        if count==0: break\n"
    "        digest.update(chunk if count==chunk_bytes else view[:count])\n"
    "    token='O' if digest.digest()==expected else 'F'\n"
    "except MemoryError as error:\n"
    "    primary=error\n"
    "    raise\n"
    "except OSError as error:\n"
    "    code=error.args[0] if error.args else -1\n"
    "    token='M' if code==2 else 'F'\n"
    "except Exception:\n"
    "    token='F'\n"
    "finally:\n"
    "    if stream is not None:\n"
    "        try:\n"
    "            stream.close()\n"
    "        except Exception:\n"
    "            if primary is None: raise\n"
    "print(token)")

OWNED_TREE_READ_CHUNK_CODE = (
    "import binascii\n"
    "path={path}\n"
    "offset={offset}\n"
    "chunk_bytes=" + str(_OWNED_TREE_CHUNK_BYTES) + "\n"
    "stream=None\n"
    "primary=None\n"
    "token='F'\n"
    "try:\n"
    "    if offset<0: raise ValueError('offset')\n"
    "    chunk=bytearray(chunk_bytes)\n"
    "    stream=open(path,'rb')\n"
    "    stream.seek(offset)\n"
    "    count=stream.readinto(chunk)\n"
    "    if count is None or count<0 or count>chunk_bytes:\n"
    "        raise ValueError('readinto')\n"
    "    if count==0:\n"
    "        token='E'\n"
    "    else:\n"
    "        token='D'+binascii.hexlify(memoryview(chunk)[:count]).decode()\n"
    "except MemoryError as error:\n"
    "    primary=error\n"
    "    raise\n"
    "except OSError as error:\n"
    "    code=error.args[0] if error.args else -1\n"
    "    token='M' if code==2 else 'F'\n"
    "except Exception:\n"
    "    token='F'\n"
    "finally:\n"
    "    if stream is not None:\n"
    "        try:\n"
    "            stream.close()\n"
    "        except Exception:\n"
    "            if primary is None: raise\n"
    "print(token)")

OWNED_TREE_DIRECTORY_COUNT_CODE = (
    "import os\n"
    "path={path}\n"
    "limit=" + str(_OWNED_TREE_MAX_DIRECTORY_ENTRIES) + "\n"
    "try:\n"
    "    mode=os.stat(path)[0]\n"
    "except OSError as error:\n"
    "    code=error.args[0] if error.args else -1\n"
    "    print('M' if code==2 else 'F')\n"
    "else:\n"
    "    if not mode&0x4000:\n"
    "        print('F')\n"
    "    else:\n"
    "        count=0\n"
    "        try:\n"
    "            for entry in os.ilistdir(path):\n"
    "                count+=1\n"
    "                if count>limit: break\n"
    "        except MemoryError:\n"
    "            raise\n"
    "        except Exception:\n"
    "            count=-1\n"
    "        print(('N%03x'%count) if 0<=count<=limit else 'F')")

OWNED_TREE_ENTRY_KIND_CODE = (
    "import os\n"
    "parent={parent}\n"
    "name={name}\n"
    "limit=" + str(_OWNED_TREE_MAX_DIRECTORY_ENTRIES) + "\n"
    "try:\n"
    "    mode=os.stat(parent)[0]\n"
    "except OSError as error:\n"
    "    code=error.args[0] if error.args else -1\n"
    "    print('M' if code==2 else 'F')\n"
    "else:\n"
    "    if not mode&0x4000:\n"
    "        print('F')\n"
    "    else:\n"
    "        count=0\n"
    "        found=None\n"
    "        try:\n"
    "            for entry in os.ilistdir(parent):\n"
    "                count+=1\n"
    "                if count>limit:\n"
    "                    found='F'\n"
    "                    break\n"
    "                if entry[0]==name:\n"
    "                    if found is not None:\n"
    "                        found='F'\n"
    "                        break\n"
    "                    found=entry[1]\n"
    "        except MemoryError:\n"
    "            raise\n"
    "        except Exception:\n"
    "            found='F'\n"
    "        if found is None:\n"
    "            print('M')\n"
    "        elif found=='F':\n"
    "            print('F')\n"
    "        else:\n"
    "            print('D' if found&0x4000 else 'R')")

OWNED_TREE_REMOVE_FILE_CODE = (
    "import os\n"
    "path={path}\n"
    "try:\n"
    "    os.remove(path)\n"
    "except MemoryError:\n"
    "    raise\n"
    "except OSError as error:\n"
    "    code=error.args[0] if error.args else -1\n"
    "    print('M' if code==2 else 'F')\n"
    "else:\n"
    "    print('E')")

OWNED_TREE_REMOVE_DIRECTORY_CODE = (
    "import os\n"
    "path={path}\n"
    "try:\n"
    "    os.rmdir(path)\n"
    "except MemoryError:\n"
    "    raise\n"
    "except OSError as error:\n"
    "    code=error.args[0] if error.args else -1\n"
    "    print('M' if code==2 else 'F')\n"
    "else:\n"
    "    print('E')")

OWNED_TREE_REMOVE_BATCH_CODE = (
    "import os\n"
    "files={files}\n"
    "directories={directories}\n"
    "root={root}\n"
    "ok=False\n"
    "def _remove(path,directory=False):\n"
    "    try:\n"
    "        os.rmdir(path) if directory else os.remove(path)\n"
    "    except OSError as error:\n"
    "        code=error.args[0] if error.args else -1\n"
    "        if code!=2: raise\n"
    "try:\n"
    "    for path in files: _remove(path)\n"
    "    for path in directories: _remove(path,True)\n"
    "    _remove(root,True)\n"
    "    ok=True\n"
    "except MemoryError:\n"
    "    raise\n"
    "except Exception:\n"
    "    pass\n"
    "print('E' if ok else 'F')")

OWNED_TREE_ACTIVATE_CODE = (
    "import os\n"
    "src={src}\n"
    "dst={dst}\n"
    "try:\n"
    "    if not (os.stat(src)[0]&0x4000):\n"
    "        print('F')\n"
    "    else:\n"
    "        try:\n"
    "            os.stat(dst)\n"
    "        except OSError as error:\n"
    "            code=error.args[0] if error.args else -1\n"
    "            if code!=2:\n"
    "                print('F')\n"
    "            else:\n"
    "                os.rename(src,dst)\n"
    "                print('E')\n"
    "        else:\n"
    "            print('C')\n"
    "except MemoryError:\n"
    "    raise\n"
    "except Exception:\n"
    "    print('F')")

_BOOT_PREFIXES = (
    "BOOT_VERSION ",
    "BOOT_RUNTIME_READY ",
    "BOOT_ROOT_VISIBLE ",
    "BOOT_BUFFERS ",
    "BOOT_MODE ",
    "BOOT_ABI_VIPER ",
)

# Six fixed BOOT_ lines; the probe buffer contract allows exactly one
# main:8192:<id> triple, so 512 bytes covers the whole report with margin.
SMOKE_REPORT_MAX_BYTES = 512
RELEASE_CONTROL_COLLECT_CODE = (
    "import gc\n"
    "g=globals()\n"
    "g.pop('_viper_identity',None)\n"
    "g.pop('_resident_binding',None)\n"
    "g.pop('run',None)\n"
    "g.pop('micropython',None)\n"
    "g=None\n"
    "gc.collect()\n"
    "print('OK')")
RELEASE_CONTROL_COLLECT_MAX_BYTES = 8


def _entry_to_ref(entry):
    return SlotRef(
        entry.name,
        entry.release_id,
        binascii.hexlify(entry.manifest_sha256).decode())


def _ref_to_entry(ref):
    return bootsel.SlotEntry(
        ref.name, ref.release_id, binascii.unhexlify(ref.manifest_sha256))


def _device_path(zone, relative_path):
    if zone == "sd":
        return "/sd/" + relative_path
    return "/" + relative_path


@dataclass(frozen=True, slots=True)
class _OwnedDirectory:
    relative_path: str
    entries: tuple


@dataclass(frozen=True, slots=True)
class _OwnedTreeSpec:
    root: str
    release_id: str
    manifest_sha256: str
    manifest_bytes: bytes
    asset_paths: tuple
    asset_sha256: tuple
    directories: tuple
    owner_payload: bytes
    owner_sha256: str


@dataclass(frozen=True, slots=True)
class _StageClaim:
    root: str
    release_id: str
    manifest_sha256: str


def _owned_full_path(root, relative_path):
    if not relative_path:
        return root
    return root + "/" + relative_path


def _owned_tree_spec(root, release_id, manifest_sha256, manifest_bytes):
    """Build one bounded, exact slot shape from a canonical manifest."""
    if (type(root) is not str or type(release_id) is not str
            or type(manifest_sha256) is not str
            or type(manifest_bytes) is not bytes):
        raise ValueError("invalid owned release tree identity")
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
        raise ValueError("owned release manifest digest mismatch")
    manifest = _validated_manifest(manifest_bytes)
    if manifest["release_id"] != release_id:
        raise ValueError("owned release manifest identity mismatch")

    directories = {"": {}}
    asset_paths = []
    asset_sha256 = []

    def add_file(relative_path):
        if (type(relative_path) is not str or not relative_path
                or relative_path.startswith("/")
                or relative_path.endswith("/")):
            raise ValueError("invalid owned release path")
        parts = relative_path.split("/")
        if (len(parts) > _OWNED_TREE_MAX_DEPTH
                or any(not part for part in parts)):
            raise ValueError("owned release path depth exceeds limit")
        parent = ""
        for index, name in enumerate(parts):
            is_directory = index + 1 < len(parts)
            entries = directories[parent]
            folded = name.casefold()
            existing = entries.get(folded)
            if existing is not None:
                if existing != (name, is_directory):
                    raise ValueError("owned release path collision")
            else:
                entries[folded] = (name, is_directory)
            if is_directory:
                parent = name if not parent else parent + "/" + name
                if parent not in directories:
                    if len(directories) >= _VERIFY_MANIFEST_MAX_RECORDS:
                        raise ValueError("owned release has too many directories")
                    directories[parent] = {}

    add_file(bootenv.MANIFEST_NAME)
    add_file(OWNER_MARKER_NAME)
    for record in manifest["assets"]:
        if (record["role"] == "managed_release"
                and record["zone"] == "sd"):
            relative_path = record["path"]
            add_file(relative_path)
            asset_paths.append(relative_path)
            asset_sha256.append((relative_path, record["sha256"]))
    if not asset_paths:
        raise ValueError("owned release has no managed assets")

    owner_payload = owner_marker_payload(release_id, manifest_sha256)
    result = []
    for relative_path, entries in directories.items():
        if len(entries) > _OWNED_TREE_MAX_DIRECTORY_ENTRIES:
            raise ValueError("owned release directory entry limit exceeded")
        result.append(_OwnedDirectory(
            relative_path,
            tuple(sorted(entries.values())),
        ))
    return _OwnedTreeSpec(
        root=root,
        release_id=release_id,
        manifest_sha256=manifest_sha256,
        manifest_bytes=manifest_bytes,
        asset_paths=tuple(sorted(asset_paths)),
        asset_sha256=tuple(sorted(asset_sha256)),
        directories=tuple(sorted(
            result,
            key=lambda item: (item.relative_path.count("/"),
                              item.relative_path),
        )),
        owner_payload=owner_payload,
        owner_sha256=hashlib.sha256(owner_payload).hexdigest(),
    )


class _OwnedReleaseTrees:
    """The only release path that may reclaim A/B or staging content."""

    def __init__(self, device, verify_slot_assets):
        self._device = device
        self._verify_slot_assets = verify_slot_assets

    @staticmethod
    def _slot_root(name):
        if name not in ("A", "B"):
            raise ValueError("owned release slot must be A or B")
        return bootenv.SLOT_BASE + "/" + name

    @staticmethod
    def _stage_root(release_id):
        if (type(release_id) is not str or len(release_id) != 64
                or any(char not in _LOWER_HEX for char in release_id)):
            raise ValueError("invalid owned release staging identity")
        return _STAGING_ROOT + "/" + release_id

    def _exec_token(self, code, **params):
        text = self._device.exec_limited(
            code, _OWNED_TREE_CONTROL_RECEIPT_MAX_BYTES, **params)
        if (type(text) is not str
                or len(text) > _OWNED_TREE_CONTROL_RECEIPT_MAX_BYTES
                or not text.isascii()):
            raise ValueError("invalid owned release receipt")
        return text.strip()

    def _root_receipt(self, spec):
        token = self._exec_token(
            OWNED_TREE_RECEIPT_CODE,
            root=repr(spec.root),
            manifest_name=repr(bootenv.MANIFEST_NAME),
            manifest_sha256=spec.manifest_sha256,
            owner_name=repr(OWNER_MARKER_NAME),
            owner_sha256=spec.owner_sha256,
        )
        if token not in ("M", "O", "F"):
            raise ValueError("invalid owned release root receipt")
        return token

    def _directory_count(self, path):
        token = self._exec_token(
            OWNED_TREE_DIRECTORY_COUNT_CODE, path=repr(path))
        if token == "M":
            return None
        if (len(token) != 4 or token[0] != "N"
                or any(char not in _LOWER_HEX for char in token[1:])):
            raise ValueError("owned release directory audit failed")
        count = int(token[1:], 16)
        if count > _OWNED_TREE_MAX_DIRECTORY_ENTRIES:
            raise ValueError("owned release directory exceeds entry limit")
        return count

    def _entry_kind(self, parent, name):
        if (type(name) is not str or not name or "/" in name
                or "\\" in name or "\x00" in name or not name.isascii()):
            raise ValueError("invalid owned release entry name")
        token = self._exec_token(
            OWNED_TREE_ENTRY_KIND_CODE,
            parent=repr(parent), name=repr(name))
        if token not in ("M", "R", "D", "F"):
            raise ValueError("invalid owned release entry receipt")
        if token == "F":
            raise ValueError("owned release entry audit failed")
        return token

    def _read_manifest(self, root, release_id, manifest_sha256):
        chunks = []
        total = 0
        for index in range(_VERIFY_MANIFEST_MAX_READS):
            text = self._device.exec_limited(
                OWNED_TREE_READ_CHUNK_CODE,
                _OWNED_TREE_CHUNK_RECEIPT_MAX_BYTES,
                path=repr(_owned_full_path(root, bootenv.MANIFEST_NAME)),
                offset=index * _OWNED_TREE_CHUNK_BYTES,
            )
            if (type(text) is not str
                    or len(text) > _OWNED_TREE_CHUNK_RECEIPT_MAX_BYTES
                    or not text.isascii()):
                raise ValueError("invalid owned release manifest chunk")
            token = text.strip()
            if token == "E":
                break
            if (not token.startswith("D") or len(token) <= 1
                    or len(token[1:]) % 2
                    or any(char not in _LOWER_HEX for char in token[1:])):
                raise ValueError("owned release manifest read failed")
            chunk = binascii.unhexlify(token[1:])
            if not chunk or len(chunk) > _OWNED_TREE_CHUNK_BYTES:
                raise ValueError("invalid owned release manifest chunk")
            total += len(chunk)
            if total > _VERIFY_MANIFEST_MAX_BYTES:
                raise ValueError("owned release manifest exceeds limit")
            chunks.append(chunk)
        else:
            raise ValueError("owned release manifest read limit exceeded")
        manifest_bytes = b"".join(chunks)
        if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
            raise ValueError("owned release manifest hash mismatch")
        spec = _owned_tree_spec(
            root, release_id, manifest_sha256, manifest_bytes)
        return spec

    def _audit_shape(self, spec, allow_missing_assets=False,
                     allow_missing_manifest=False,
                     allow_missing_owner=False):
        present_files = set()
        present_directories = set()
        directory_by_path = {
            directory.relative_path: directory for directory in spec.directories}

        def optional_file(relative_path):
            if relative_path == OWNER_MARKER_NAME:
                return allow_missing_owner
            if relative_path == bootenv.MANIFEST_NAME:
                return allow_missing_manifest
            return allow_missing_assets

        def audit(directory):
            path = _owned_full_path(spec.root, directory.relative_path)
            actual_count = self._directory_count(path)
            if actual_count is None:
                raise ValueError("owned release directory disappeared")
            present_directories.add(directory.relative_path)
            expected_count = 0
            descendants = []
            for name, is_directory in directory.entries:
                relative_path = name if not directory.relative_path else (
                    directory.relative_path + "/" + name)
                kind = self._entry_kind(path, name)
                if kind == "M":
                    if is_directory:
                        if not allow_missing_assets:
                            raise ValueError("owned release directory is missing")
                    elif not optional_file(relative_path):
                        raise ValueError("owned release file is missing")
                    continue
                expected_count += 1
                if is_directory:
                    if kind != "D":
                        raise ValueError("owned release path type conflict")
                    descendants.append(relative_path)
                else:
                    if kind != "R":
                        raise ValueError("owned release path type conflict")
                    present_files.add(relative_path)
            if actual_count != expected_count:
                raise ValueError("owned release contains unknown content")
            for relative_path in descendants:
                audit(directory_by_path[relative_path])

        audit(directory_by_path[""])
        return frozenset(present_files), frozenset(present_directories)

    def _verify_spec(self, spec, allow_missing_assets=False):
        if self._root_receipt(spec) != "O":
            raise ValueError("owned release marker or manifest is not trusted")
        present_files, present_directories = self._audit_shape(
            spec, allow_missing_assets=allow_missing_assets)
        all_assets_present = all(
            path in present_files for path in spec.asset_paths)
        if not allow_missing_assets or all_assets_present:
            self._verify_slot_assets(
                spec.root, spec.manifest_sha256, spec.release_id)
        else:
            for relative_path, sha256 in spec.asset_sha256:
                if relative_path not in present_files:
                    continue
                token = self._exec_token(
                    OWNED_TREE_FILE_RECEIPT_CODE,
                    path=repr(_owned_full_path(spec.root, relative_path)),
                    sha256=sha256)
                if token != "O":
                    raise ValueError("owned release asset is not trusted")
        return present_files, present_directories

    def _delete_file(self, path):
        token = self._exec_token(OWNED_TREE_REMOVE_FILE_CODE, path=repr(path))
        if token != "E":
            raise ValueError("owned release file erase failed")

    def _delete_directory(self, path, root=False):
        token = self._exec_token(
            OWNED_TREE_REMOVE_DIRECTORY_CODE, path=repr(path))
        if token == "E" or (root and token == "M"):
            return
        raise ValueError("owned release directory erase failed")

    def _erase_verified(self, spec, present_files, present_directories):
        files = tuple(
            _owned_full_path(spec.root, relative_path)
            for relative_path in (
                spec.asset_paths
                + (bootenv.MANIFEST_NAME, OWNER_MARKER_NAME))
            if relative_path in present_files)
        directories = tuple(
            _owned_full_path(spec.root, directory)
            for directory in sorted(
                present_directories - {""},
                key=lambda path: (path.count("/"), path), reverse=True))
        token = self._exec_token(
            OWNED_TREE_REMOVE_BATCH_CODE,
            files=repr(files), directories=repr(directories),
            root=repr(spec.root))
        if token != "E":
            raise ValueError("owned release batch erase failed")
        return "ERASED"

    def _marker_only_erase(self, root, release_id, manifest_sha256):
        marker_path = _owned_full_path(root, OWNER_MARKER_NAME)
        owner_sha256 = hashlib.sha256(
            owner_marker_payload(release_id, manifest_sha256)).hexdigest()
        receipt = stream_hash_receipt(self._device, ((marker_path, owner_sha256),))
        if receipt.fault or receipt.missing_mask or receipt.matched_mask != 1:
            raise ValueError("owned staging marker is not trusted")
        if self._directory_count(root) != 1:
            raise ValueError("owned staging contains unknown content")
        if self._entry_kind(root, OWNER_MARKER_NAME) != "R":
            raise ValueError("owned staging marker changed type")
        self._delete_file(marker_path)
        self._delete_directory(root, root=True)
        return "ERASED"

    def stage(self, plan):
        root = self._stage_root(plan.release_id)
        spec = _owned_tree_spec(
            root, plan.release_id, plan.manifest_sha256, plan.manifest_bytes)
        claim = _StageClaim(root, plan.release_id, plan.manifest_sha256)
        root_kind = self._entry_kind(_STAGING_ROOT, plan.release_id)
        if root_kind == "M":
            self._device.write_file(
                _owned_full_path(root, OWNER_MARKER_NAME), spec.owner_payload)
        elif root_kind != "D":
            raise ValueError("owned staging root type conflict")
        else:
            marker_kind = self._entry_kind(root, OWNER_MARKER_NAME)
            manifest_kind = self._entry_kind(root, bootenv.MANIFEST_NAME)
            if marker_kind != "R":
                raise ValueError("owned staging marker is missing")
            if manifest_kind == "M":
                self._marker_only_erase(root, plan.release_id, plan.manifest_sha256)
                self._device.write_file(
                    _owned_full_path(root, OWNER_MARKER_NAME),
                    spec.owner_payload)
            elif manifest_kind != "R":
                raise ValueError("owned staging manifest type conflict")
            else:
                self._verify_spec(spec, allow_missing_assets=True)

        marker_hash = stream_hash_receipt(
            self._device,
            ((_owned_full_path(root, OWNER_MARKER_NAME), spec.owner_sha256),),
        )
        if (marker_hash.fault or marker_hash.missing_mask
                or marker_hash.matched_mask != 1):
            raise ValueError("owned staging marker verification failed")
        manifest_kind = self._entry_kind(root, bootenv.MANIFEST_NAME)
        if manifest_kind == "M":
            self._device.write_file(
                _owned_full_path(root, bootenv.MANIFEST_NAME), plan.manifest_bytes)
        elif manifest_kind != "R":
            raise ValueError("owned staging manifest type conflict")
        else:
            self._verify_spec(spec, allow_missing_assets=True)

        present_files, _directories = self._audit_shape(
            spec, allow_missing_assets=True)
        for relative_path in spec.asset_paths:
            if relative_path not in present_files:
                asset = next(
                    item for item in plan.assets
                    if (item.role == "managed_release" and item.zone == "sd"
                        and item.relative_path == relative_path))
                self._device.write_file(
                    _owned_full_path(root, relative_path), asset.payload)
        return claim

    def verify(self, claim, plan):
        spec = _owned_tree_spec(
            claim.root, plan.release_id, plan.manifest_sha256,
            plan.manifest_bytes)
        if (claim.release_id != spec.release_id
                or claim.manifest_sha256 != spec.manifest_sha256):
            raise ValueError("owned staging claim conflicts with release plan")
        self._verify_spec(spec)

    def activate(self, claim, plan, slot_name):
        # release_apply calls verify immediately before select_trial in the
        # same guarded session; no device write can occur between the calls.
        destination = self._slot_root(slot_name)
        self._device.makedirs(bootenv.SLOT_BASE)
        if self._entry_kind(bootenv.SLOT_BASE, slot_name) != "M":
            raise ValueError("candidate slot is already occupied")
        token = self._exec_token(
            OWNED_TREE_ACTIVATE_CODE,
            src=repr(claim.root), dst=repr(destination))
        if token != "E":
            raise ValueError("owned staging activation failed")

    def discard_stage(self, claim, plan):
        spec = _owned_tree_spec(
            claim.root, plan.release_id, plan.manifest_sha256,
            plan.manifest_bytes)
        root_kind = self._entry_kind(_STAGING_ROOT, plan.release_id)
        if root_kind == "M":
            return "ABSENT"
        if root_kind != "D":
            raise ValueError("owned staging root type conflict")
        marker_kind = self._entry_kind(spec.root, OWNER_MARKER_NAME)
        manifest_kind = self._entry_kind(spec.root, bootenv.MANIFEST_NAME)
        if marker_kind != "R":
            raise ValueError("owned staging marker is missing")
        if manifest_kind == "M":
            return self._marker_only_erase(
                spec.root, spec.release_id, spec.manifest_sha256)
        if manifest_kind != "R":
            raise ValueError("owned staging manifest type conflict")
        present_files, present_directories = self._verify_spec(
            spec, allow_missing_assets=True)
        return self._erase_verified(spec, present_files, present_directories)

    def verify_slot(self, ref, plan=None):
        if type(ref) is not SlotRef:
            raise ValueError("invalid owned release slot reference")
        root = self._slot_root(ref.name)
        if plan is None:
            provisional = _OwnedTreeSpec(
                root=root,
                release_id=ref.release_id,
                manifest_sha256=ref.manifest_sha256,
                manifest_bytes=b"",
                asset_paths=(),
                asset_sha256=(),
                directories=(),
                owner_payload=owner_marker_payload(
                    ref.release_id, ref.manifest_sha256),
                owner_sha256=hashlib.sha256(owner_marker_payload(
                    ref.release_id, ref.manifest_sha256)).hexdigest(),
            )
            if self._root_receipt(provisional) != "O":
                raise ValueError("owned slot marker or manifest is not trusted")
            spec = self._read_manifest(
                root, ref.release_id, ref.manifest_sha256)
        else:
            if (ref.release_id != plan.release_id
                    or ref.manifest_sha256 != plan.manifest_sha256):
                raise ValueError("owned slot conflicts with release plan")
            spec = _owned_tree_spec(
                root, plan.release_id, plan.manifest_sha256,
                plan.manifest_bytes)
        self._verify_spec(spec)

    def erase_retired(self, ref):
        if type(ref) is not SlotRef:
            raise ValueError("invalid retired slot reference")
        root = self._slot_root(ref.name)
        root_kind = self._entry_kind(bootenv.SLOT_BASE, ref.name)
        if root_kind == "M":
            return "ABSENT"
        if root_kind != "D":
            raise ValueError("retired slot root type conflict")
        provisional = _OwnedTreeSpec(
            root=root,
            release_id=ref.release_id,
            manifest_sha256=ref.manifest_sha256,
            manifest_bytes=b"",
            asset_paths=(),
            asset_sha256=(),
            directories=(),
            owner_payload=owner_marker_payload(
                ref.release_id, ref.manifest_sha256),
            owner_sha256=hashlib.sha256(owner_marker_payload(
                ref.release_id, ref.manifest_sha256)).hexdigest(),
        )
        root_receipt = self._root_receipt(provisional)
        if root_receipt == "M":
            return "ABSENT"
        if root_receipt != "O":
            raise ValueError("retired slot marker or manifest is not trusted")
        spec = self._read_manifest(
            root, ref.release_id, ref.manifest_sha256)
        present_files, present_directories = self._verify_spec(
            spec, allow_missing_assets=True)
        return self._erase_verified(spec, present_files, present_directories)


class _MpremoteSession:
    def __init__(self, device, probe_source):
        self._device = device
        self._probe_source = probe_source
        self._trees = _OwnedReleaseTrees(device, self._verify_slot_assets)
        self._staged_slot = None
        self._staged_claim = None
        self._staged_plan = None
        self._reset = False
        self._closed = False

    def _read_record_pair(self, paths, unpack_record, max_bytes):
        winner = None
        winner_index = -1
        for index, path in enumerate(paths):
            raw = self._device.read_file(path)
            if (not isinstance(raw, (bytes, bytearray))
                    or len(raw) > max_bytes):
                continue
            record = unpack_record(raw)
            if (record is not None
                    and (winner is None
                         or record.generation > winner.generation)):
                winner = record
                winner_index = index
        return winner, winner_index

    def _read_selector_pair(self):
        return self._read_record_pair(
            (_SEL0, _SEL1), bootsel.unpack_record,
            _SELECTOR_RECORD_MAX_BYTES)

    def _read_selector(self):
        record, _index = self._read_selector_pair()
        return record

    def _write_selector(self, selector):
        winner, winner_index = self._read_selector_pair()
        generation = 1 if winner is None else winner.generation + 1
        trial_generation = selector.trial_generation
        if selector.trial is not None and trial_generation == 0:
            trial_generation = generation
        stored = bootsel.SelectorData(
            generation,
            selector.confirmed,
            selector.trial,
            trial_generation,
            selector.trial_consumed,
            selector.retired,
            selector.confirmation_pending,
        )
        packed = bootsel.pack_record(stored)
        target = _SEL1 if winner_index == 0 else _SEL0
        self._device.write_file(target, packed)
        if self._device.read_file(target) != packed:
            raise OSError("selector record read-back mismatch")
        return stored

    def _read_boot_entry(self):
        entry, _index = self._read_record_pair(
            (_LOG0, _LOG1), bootlog.unpack_record,
            _BOOTLOG_RECORD_MAX_BYTES)
        return entry

    def _hash_receipt(self, pairs):
        return stream_hash_receipt(self._device, pairs)

    @staticmethod
    def _slot_root(name):
        return bootenv.SLOT_BASE + "/" + name

    def _slot_manifest_path(self, name):
        return self._slot_root(name) + "/" + bootenv.MANIFEST_NAME

    def _validate_slot_manifest(self, ref, expected_bytes=None):
        expected_sha256 = ref.manifest_sha256
        if expected_bytes is not None:
            expected_sha256 = hashlib.sha256(expected_bytes).hexdigest()
            if expected_sha256 != ref.manifest_sha256:
                raise ValueError("slot manifest bytes mismatch")
        out = self._device.exec_limited(
            VALIDATE_MANIFEST_CODE,
            VALIDATE_MANIFEST_RECEIPT_MAX_BYTES,
            manifest_path=self._slot_manifest_path(ref.name),
            manifest_sha256=expected_sha256,
        ).strip()
        if out == "OK":
            return
        if out == "MISSING":
            raise ValueError("slot manifest is missing")
        if out == "HASH":
            raise ValueError("slot manifest hash mismatch")
        raise ValueError("slot manifest validation failed")

    def _verify_slot_assets(self, slot_root, plan_or_manifest_sha256,
                            release_id=None):
        if release_id is None:
            manifest_sha256 = plan_or_manifest_sha256.manifest_sha256
        else:
            manifest_sha256 = plan_or_manifest_sha256
            if (type(release_id) is not str or len(release_id) != 64
                    or any(char not in _LOWER_HEX for char in release_id)):
                raise ValueError("invalid owned release identity")
        timed_exec = getattr(self._device, "exec_limited_timeout", None)
        if timed_exec is None:
            out = self._device.exec_limited(
                VERIFY_SLOT_CODE,
                VERIFY_SLOT_RECEIPT_MAX_BYTES,
                slot_root=slot_root,
                manifest_name=bootenv.MANIFEST_NAME,
                manifest_sha256=manifest_sha256,
            )
        else:
            out = timed_exec(
                VERIFY_SLOT_CODE,
                VERIFY_SLOT_RECEIPT_MAX_BYTES,
                VERIFY_SLOT_TIMEOUT_S,
                slot_root=slot_root,
                manifest_name=bootenv.MANIFEST_NAME,
                manifest_sha256=manifest_sha256,
            )
        out = out.strip()
        if out != "OK":
            raise ValueError("slot asset verification failed: " + out)

    def _erase_retired(self, selector, ref):
        if not isinstance(ref, SlotRef):
            raise ValueError("invalid retired slot reference")
        retired = tuple(_entry_to_ref(entry) for entry in selector.retired)
        if ref not in retired:
            raise ValueError("slot is not selector-retired")
        if (selector.confirmed is not None
                and _entry_to_ref(selector.confirmed) == ref):
            raise ValueError("confirmed slot cannot be erased")
        if (selector.trial is not None
                and _entry_to_ref(selector.trial) == ref):
            raise ValueError("trial slot cannot be erased")
        receipt = self._trees.erase_retired(ref)
        if receipt not in ("ERASED", "ABSENT"):
            raise ValueError("retired slot erase returned an invalid receipt")
        return receipt

    def resume_confirmed(self, plan):
        selector = self._read_selector()
        confirmed = selector.confirmed if selector else None
        if confirmed is None or confirmed.release_id != plan.release_id:
            return None
        ref = _entry_to_ref(confirmed)
        if ref.manifest_sha256 != plan.manifest_sha256:
            raise ValueError(
                "confirmed release identity conflicts with local plan")
        self._trees.verify_slot(ref, plan)
        return SelectionTicket(
            selector.generation, ref, already_confirmed=True)

    def resume_trial(self, plan):
        selector = self._read_selector()
        trial = selector.trial if selector else None
        if trial is None:
            return None
        ref = _entry_to_ref(trial)
        if (ref.release_id != plan.release_id
                or ref.manifest_sha256 != plan.manifest_sha256):
            self.reject_trial(
                SelectionTicket(selector.trial_generation, ref))
            return None
        self._trees.verify_slot(ref, plan)
        if selector.trial_consumed:
            stored = self._write_selector(bootsel.SelectorData(
                0,
                selector.confirmed,
                trial,
                0,
                False,
                selector.retired,
                selector.confirmation_pending))
            return SelectionTicket(stored.generation, ref)
        return SelectionTicket(selector.trial_generation, ref)

    def resume_cleanup(self):
        selector = self._read_selector()
        if selector is not None and selector.confirmation_pending:
            raise ValueError(
                "pending confirmation must be resolved before staging")
        if selector is not None and selector.trial is not None:
            raise ValueError(
                "pending trial must be resolved before staging")
        if selector is None or not selector.retired:
            return
        for entry in selector.retired:
            ref = _entry_to_ref(entry)
            self._erase_retired(selector, ref)
        self._write_selector(bootsel.SelectorData(
            0, selector.confirmed, None, 0, False, (), False))

    def validate_bootstrap(self, plan):
        pairs = tuple(
            (_device_path(asset.zone, asset.relative_path), asset.sha256)
            for asset in plan.assets
            if asset.role == "bootstrap_fixed")
        if pairs:
            receipt = self._hash_receipt(pairs)
            expected_mask = (1 << len(pairs)) - 1
            if (receipt.fault
                    or receipt.missing_mask
                    or receipt.matched_mask != expected_mask):
                raise ValueError(
                    "stable bootstrap anchor verification failed")

    def sync_confirmed(self, plan):
        """Update one already-provisioned confirmed slot in place."""
        selector = self._read_selector()
        if (selector is None or selector.confirmed is None
                or selector.trial is not None
                or selector.confirmation_pending):
            raise ValueError(
                "fast deployment requires one stable confirmed slot; "
                "use --transactional to provision or repair it")
        current_ref = _entry_to_ref(selector.confirmed)
        root = self._trees._slot_root(current_ref.name)
        manifest_path = _owned_full_path(root, bootenv.MANIFEST_NAME)
        manifest_bytes = self._device.read_file(manifest_path)
        if manifest_bytes is None:
            raise ValueError("confirmed slot manifest is missing")
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        if manifest_sha256 not in (
                current_ref.manifest_sha256, plan.manifest_sha256):
            raise ValueError("confirmed slot manifest hash mismatch")
        manifest = _validated_manifest(manifest_bytes)
        expected_release_id = (
            plan.release_id
            if manifest_sha256 == plan.manifest_sha256
            else current_ref.release_id)
        if manifest["release_id"] != expected_release_id:
            raise ValueError("confirmed slot manifest identity mismatch")

        owner_path = _owned_full_path(root, OWNER_MARKER_NAME)
        owner = self._device.read_file(owner_path)
        expected_owner = owner_marker_payload(
            manifest["release_id"], manifest_sha256)
        previous_owner = owner_marker_payload(
            current_ref.release_id, current_ref.manifest_sha256)
        if owner not in (expected_owner, previous_owner):
            raise ValueError("confirmed slot owner marker mismatch")

        self.validate_bootstrap(plan)
        current_hashes = {
            record["path"]: record["sha256"]
            for record in manifest["assets"]
            if record["role"] == "managed_release"
            and record["zone"] == "sd"
        }
        managed = tuple(
            asset for asset in plan.assets
            if asset.role == "managed_release" and asset.zone == "sd")
        for asset in managed:
            if (asset.relative_path not in current_hashes
                    and self._device.exists(
                        _owned_full_path(root, asset.relative_path))):
                raise ValueError(
                    "new managed path conflicts with an unowned file; "
                    "use --transactional to preserve it")
        for asset in managed:
            if current_hashes.get(asset.relative_path) != asset.sha256:
                self._device.write_file(
                    _owned_full_path(root, asset.relative_path),
                    asset.payload)

        for zone, relative_path in cleanup_candidates(
                manifest_bytes, manifest_sha256, plan):
            if zone != "sd":
                continue
            token = self._trees._exec_token(
                OWNED_TREE_REMOVE_FILE_CODE,
                path=repr(_owned_full_path(root, relative_path)))
            if token not in ("E", "M"):
                raise ValueError("obsolete managed file erase failed")

        if (manifest_sha256 != plan.manifest_sha256
                or owner != owner_marker_payload(
                    plan.release_id, plan.manifest_sha256)):
            self._device.write_file(manifest_path, plan.manifest_bytes)
            self._device.write_file(
                owner_path,
                owner_marker_payload(plan.release_id, plan.manifest_sha256))

        new_entry = bootsel.SlotEntry(
            current_ref.name, plan.release_id,
            binascii.unhexlify(plan.manifest_sha256))
        if selector.confirmed != new_entry:
            selector = self._write_selector(bootsel.SelectorData(
                0, new_entry, None, 0, False, selector.retired, False))

        for asset in plan.assets:
            if asset.role == "seed_if_absent":
                path = _device_path(asset.zone, asset.relative_path)
                if not self._device.exists(path):
                    self._device.write_file(path, asset.payload)
        return SelectionTicket(
            selector.generation,
            SlotRef(
                current_ref.name, plan.release_id, plan.manifest_sha256),
            already_confirmed=True)

    def stage(self, plan):
        selector = self._read_selector()
        if selector is not None and selector.trial is not None:
            raise ValueError("another trial selection is still pending")
        confirmed = selector.confirmed if selector else None
        if confirmed is None:
            slot_name = "A"
        else:
            slot_name = "B" if confirmed.name == "A" else "A"
        if selector is not None and any(
                entry.name == slot_name for entry in selector.retired):
            raise ValueError(
                "previous retired slot must be finalized before staging")
        self._staged_claim = self._trees.stage(plan)
        self._staged_plan = plan
        self._staged_slot = slot_name

    def verify(self, plan):
        if self._staged_slot is None or self._staged_claim is None:
            raise ValueError("no staged release to verify")
        self._trees.verify(self._staged_claim, plan)

    def select_trial(self, plan):
        if self._staged_slot is None or self._staged_claim is None:
            raise ValueError("no staged release to activate")
        selector = self._read_selector()
        slot_name = self._staged_slot
        self._trees.activate(self._staged_claim, plan, slot_name)
        self._staged_claim = None
        self._staged_plan = None
        self._staged_slot = None
        stored = self._write_selector(bootsel.SelectorData(
            0,
            selector.confirmed if selector else None,
            bootsel.SlotEntry(
                slot_name,
                plan.release_id,
                binascii.unhexlify(plan.manifest_sha256)),
            0,
            False,
            selector.retired if selector else (),
            selector.confirmation_pending if selector else False))
        return SelectionTicket(
            stored.generation, _entry_to_ref(stored.trial))

    def reconcile_trial_selection(self, plan):
        selector = self._read_selector()
        trial = selector.trial if selector else None
        if trial is None:
            return None
        ref = _entry_to_ref(trial)
        if (ref.release_id != plan.release_id
                or ref.manifest_sha256 != plan.manifest_sha256
                or selector.trial_generation is None):
            raise ValueError("trial selector readback is inconsistent")
        self._trees.verify_slot(ref, plan)
        return SelectionTicket(selector.trial_generation, ref)

    def abort_staging(self, release_id):
        claim = self._staged_claim
        plan = self._staged_plan
        if claim is not None:
            if claim.release_id != release_id or plan is None:
                raise ValueError("staged release identity conflicts with abort")
            self._trees.discard_stage(claim, plan)
        self._staged_claim = None
        self._staged_plan = None
        self._staged_slot = None

    def _run_smoke(self):
        text = self._device.exec_limited(
            self._probe_source, SMOKE_REPORT_MAX_BYTES)
        collected = self._device.exec_limited(
            RELEASE_CONTROL_COLLECT_CODE,
            RELEASE_CONTROL_COLLECT_MAX_BYTES).strip()
        if collected != "OK":
            raise ValueError("release smoke cleanup failed")
        fields = {}
        for line in text.splitlines():
            for prefix in _BOOT_PREFIXES:
                if line.startswith(prefix):
                    fields[prefix.strip()] = line[len(prefix):]
        if len(fields) != len(_BOOT_PREFIXES):
            raise ValueError("boot smoke report is incomplete")
        mode = fields["BOOT_MODE"]
        if mode == "source":
            abi_tag = SOURCE_ABI_TAG
        elif mode == "mpy" and fields["BOOT_ABI_VIPER"] == "ok":
            abi_tag = MPY_ABI_TAG
        else:
            raise ValueError("boot smoke ABI evidence failed")
        buffers = []
        for part in fields["BOOT_BUFFERS"].split(","):
            name, length, identity = part.split(":")
            buffers.append((name, int(length), int(identity)))
        return ReleaseSmokeResult(
            release_id="",
            app_version=fields["BOOT_VERSION"],
            mode=mode,
            abi_tag=abi_tag,
            resident_runtime=fields["BOOT_RUNTIME_READY"] == "True",
            root_visible=fields["BOOT_ROOT_VISIBLE"] == "True",
            buffers=tuple(buffers),
        )

    def read_boot_observation(self, ticket, trial):
        entry = self._read_boot_entry()
        if entry is None:
            raise ValueError("no boot observation recorded")
        if entry.selected is None:
            raise ValueError("cold boot recorded no selected slot")
        selected = _entry_to_ref(entry.selected)
        smoke = self._run_smoke()
        return ColdBootObservation(
            selector_generation=entry.selector_generation,
            selection_generation=entry.selection_generation,
            boot_id=entry.generation,
            selected=selected,
            smoke=ReleaseSmokeResult(
                release_id=selected.release_id,
                app_version=smoke.app_version,
                mode=smoke.mode,
                abi_tag=smoke.abi_tag,
                resident_runtime=smoke.resident_runtime,
                root_visible=smoke.root_visible,
                buffers=smoke.buffers,
            ),
        )

    def confirm_trial(self, ticket):
        selector = self._read_selector()
        trial = selector.trial if selector else None
        if (trial is None
                or _entry_to_ref(trial) != ticket.slot_ref
                or selector.trial_generation != ticket.selector_generation
                or selector.trial_consumed is not True):
            raise ValueError("trial selector ticket is not confirmable")
        retired = selector.retired
        if selector.confirmed is not None:
            retired = retired + (selector.confirmed,)
        self._write_selector(bootsel.SelectorData(
            0,
            _ref_to_entry(ticket.slot_ref),
            None,
            0,
            False,
            retired,
            True))

    def is_release_confirmed(self, ticket):
        selector = self._read_selector()
        if selector is None or selector.confirmed is None:
            return False
        if (_entry_to_ref(selector.confirmed) != ticket.slot_ref
                or selector.trial is not None
                or selector.trial_generation != 0
                or selector.trial_consumed):
            return False
        try:
            # The selector readback is a fixed sub-kilobyte record, but a full
            # slot audit is not resident work.  Reuse the same boot-only seam
            # as staging/final cleanup before hashing the confirmed tree.
            self._device.reset_to_boot_repl()
            self._trees.verify_slot(ticket.slot_ref)
        except ValueError:
            return False
        return True

    def reject_trial(self, ticket):
        selector = self._read_selector()
        if (selector is not None
                and selector.trial is not None
                and _entry_to_ref(selector.trial) == ticket.slot_ref):
            retired = selector.retired
            if selector.trial not in retired:
                retired = retired + (selector.trial,)
            self._write_selector(bootsel.SelectorData(
                0,
                selector.confirmed,
                None,
                0,
                False,
                retired,
                selector.confirmation_pending))

    def rollback_confirmation(self, ticket):
        selector = self._read_selector()
        if (selector is None
                or selector.confirmed is None
                or _entry_to_ref(selector.confirmed) != ticket.slot_ref
                or not selector.confirmation_pending):
            return False
        fallback = selector.retired[0] if selector.retired else None
        if fallback is not None:
            self._trees.verify_slot(_entry_to_ref(fallback))
        failed = selector.confirmed
        stored = self._write_selector(bootsel.SelectorData(
            0, fallback, None, 0, False, (failed,), False))
        failed_ref = _entry_to_ref(failed)
        self._erase_retired(stored, failed_ref)
        self._write_selector(bootsel.SelectorData(
            0, fallback, None, 0, False, (), False))
        return True

    def finalize_release(self, ticket, plan):
        selector = self._read_selector()
        if (selector is None
                or selector.confirmed is None
                or _entry_to_ref(selector.confirmed) != ticket.slot_ref):
            raise ValueError("release is not confirmed for finalization")
        # The confirmed smoke has just run inside the full resident app. Move
        # only the subsequent tree audit/removal into boot.py's high-headroom
        # raw REPL, then re-read the selector before deleting a retired slot.
        self._device.reset_to_boot_repl()
        selector = self._read_selector()
        if (selector is None
                or selector.confirmed is None
                or _entry_to_ref(selector.confirmed) != ticket.slot_ref):
            raise ValueError("release confirmation changed before cleanup")
        if selector.confirmation_pending:
            selector = self._write_selector(bootsel.SelectorData(
                0,
                selector.confirmed,
                None,
                0,
                False,
                selector.retired,
                False))
        for entry in selector.retired:
            ref = _entry_to_ref(entry)
            self._erase_retired(selector, ref)
        for asset in plan.assets:
            if asset.role == "seed_if_absent":
                path = _device_path(asset.zone, asset.relative_path)
                if not self._device.exists(path):
                    self._device.write_file(path, asset.payload)
        self._write_selector(bootsel.SelectorData(
            0, selector.confirmed, None, 0, False, (), False))

    def _reset_device(self):
        if self._reset:
            raise RuntimeError("release session reset more than once")
        self._reset = True
        self._device.reset()

    def _close(self):
        if self._closed:
            raise RuntimeError("release session closed more than once")
        self._closed = True
        self._device.close()


class MpremoteReleaseAdapter:
    """Applies releases through one connect/reset/close session per phase."""

    def __init__(self, device_factory, probe_source=None, boot_wait_s=8.0,
                 sleep=None):
        self._device_factory = device_factory
        if probe_source is None:
            probe_source = (
                Path(__file__).parent / "device_boot_probe.py"
            ).read_text(encoding="utf-8")
        self._probe_source = probe_source
        self._boot_wait_s = boot_wait_s
        if sleep is None:
            import time
            sleep = time.sleep
        self._sleep = sleep
        self._needs_boot_wait = False
        self._first_session = True

    def run_session(self, operation):
        if self._needs_boot_wait:
            self._sleep(self._boot_wait_s)
            self._needs_boot_wait = False
        device = self._device_factory()
        try:
            device.connect()
            if self._first_session:
                # Staging and its full-tree verification do not need the
                # resident calculator.  Enter boot.py's high-headroom raw
                # REPL before the first control operation so dozens of bounded
                # receipts cannot fragment the application heap.
                device.reset_to_boot_repl()
                self._first_session = False
        except BaseException:
            try:
                device.close()
            except BaseException:
                # Preserve the connect failure.  A failed connection may
                # still own a partially opened serial transport, so close is
                # attempted but cannot replace the primary failure.
                pass
            raise
        session = _MpremoteSession(device, self._probe_source)
        try:
            return run_guarded_session(
                lambda: operation(session),
                session._reset_device,
                session._close,
            )
        finally:
            self._needs_boot_wait = True


class MpremoteDevice:
    """Thin real-transport wrapper used by the production adapter."""

    def __init__(self, port, baudrate=115200, connect_wait=10):
        self._port = port
        self._baudrate = baudrate
        self._connect_wait = connect_wait
        self._transport = None
        self._known_directories = set()

    def connect(self):
        from mpremote.transport_serial import SerialTransport
        self._known_directories.clear()
        self._transport = SerialTransport(
            self._port, self._baudrate, wait=self._connect_wait)
        self._transport.enter_raw_repl(soft_reset=False)

    def exec(self, code, **params):
        return self._exec(code, params, None)

    def exec_limited(self, code, max_output_bytes, **params):
        if type(max_output_bytes) is not int or max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be a positive integer")
        return self._exec(code, params, max_output_bytes)

    def exec_limited_timeout(
            self, code, max_output_bytes, timeout_s, **params):
        if type(max_output_bytes) is not int or max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be a positive integer")
        if type(timeout_s) is not int or timeout_s <= 0:
            raise ValueError("timeout_s must be a positive integer")
        return self._exec(code, params, max_output_bytes, timeout_s)

    def _exec(self, code, params, max_output_bytes, timeout_s=None):
        if params:
            code = code.format(**params)
        chunks = []
        output = bytearray() if max_output_bytes is not None else None

        def consume(chunk):
            if output is None:
                chunks.append(chunk)
                return
            if len(output) + len(chunk) > max_output_bytes:
                raise ValueError("device response exceeded byte limit")
            output.extend(chunk)

        try:
            if timeout_s is None:
                self._transport.exec(code, data_consumer=consume)
            else:
                from mpremote.transport import TransportExecError
                ret, ret_err = self._transport.exec_raw(
                    code, timeout=timeout_s, data_consumer=consume)
                if ret_err:
                    raise TransportExecError(ret, ret_err.decode())
        except MemoryError:
            raise
        except Exception as error:
            raise OSError(
                "device exec failed: " + str(error)) from error
        if output is None:
            raw = b"".join(chunks)
        else:
            raw = bytes(output)
        raw = raw.replace(b"\x04", b"")
        return raw.decode("utf-8", errors="replace")

    def read_file(self, path):
        try:
            return bytes(self._transport.fs_readfile(path))
        except OSError:
            return None

    def write_file(self, path, data):
        parent = path.rsplit("/", 1)[0]
        if parent:
            self._mkdirs(parent)
        self._transport.fs_writefile(path, bytes(data))

    def _mkdirs(self, path):
        current = ""
        for part in path.strip("/").split("/"):
            current += "/" + part
            if current in self._known_directories:
                continue
            try:
                self._transport.fs_mkdir(current)
            except OSError:
                pass
            self._known_directories.add(current)

    def exists(self, path):
        return self._transport.fs_exists(path)

    def makedirs(self, path):
        self._mkdirs(path)

    def reset_to_boot_repl(self):
        self._transport.enter_raw_repl(soft_reset=True)

    def reset(self):
        try:
            self._transport.exec("import machine\nmachine.reset()")
        except Exception:
            # The connection dies with the reset; the next boot record is
            # the proof that the reset actually happened.
            pass

    def close(self):
        if self._transport is not None:
            transport = self._transport
            self._transport = None
            transport.close()
