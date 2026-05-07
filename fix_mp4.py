#!/usr/bin/env python3

# attempts to fix broken mp4 written by opencv
# Needs ffmpeg installed/in path and a "good"/successfull mp4 written by the process with the same parameters/width/codec etc.

import os
import sys
import struct
from pathlib import Path

START = b"\x00\x00\x01"
def check_video_packets(p):
    data = Path(p).read_bytes()
    for pat,name in [(b"\x00\x00\x01\xb6","VOP"), (b"\x00\x00\x01\x20","VOL"), (b"mdat","mdat")]:
        print(name, data.find(pat), data.count(pat))
    exec_ffprobe='ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,codec_tag_string,profile,width,height,pix_fmt,r_frame_rate,avg_frame_rate -of default=nw=1 '

    ffprobe_res = os.popen(exec_ffprobe+working_mp4).read() #only works for valid files
    if len(ffprobe_res) > 0:
        print("ffprobe output:\n", ffprobe_res)

def atom_children(data, start, end):
    i = start
    while i + 8 <= end:
        size = struct.unpack(">I", data[i:i+4])[0]
        typ = data[i+4:i+8].decode("latin1", errors="replace")
        header = 8
        if size == 1:
            if i + 16 > end:
                break
            size = struct.unpack(">Q", data[i+8:i+16])[0]
            header = 16
        elif size == 0:
            size = end - i
        if size < header or i + size > end:
            break
        yield i, i + size, typ, i + header
        i += size

def find_esds(data):
    def walk(start, end, path=""):
        for a0, a1, typ, payload in atom_children(data, start, end):
            if typ == "esds":
                return data[payload:a1]
            if typ in {"moov", "trak", "mdia", "minf", "stbl"}:
                r = walk(payload, a1, path + "/" + typ)
                if r:
                    return r
            elif typ == "stsd":
                # version/flags + entry_count
                r = walk(payload + 8, a1, path + "/stsd")
                if r:
                    return r
            elif typ == "mp4v":
                # VisualSampleEntry header is 78 bytes
                r = walk(payload + 78, a1, path + "/mp4v")
                if r:
                    return r
        return None
    return walk(0, len(data))

def read_descr_len(buf, i):
    val = 0
    for _ in range(4):
        if i >= len(buf):
            return None, i
        b = buf[i]
        i += 1
        val = (val << 7) | (b & 0x7F)
        if not (b & 0x80):
            return val, i
    return val, i

def scan_descriptor_block(buf):
    i = 0
    while i < len(buf):
        tag = buf[i]
        i += 1
        length, i = read_descr_len(buf, i)
        if length is None:
            return None
        payload_start = i
        payload_end = min(i + length, len(buf))
        payload = buf[payload_start:payload_end]
        if tag == 0x05:
            return payload
        if tag == 0x04 and len(payload) > 13:
            sub = scan_descriptor_block(payload[13:])
            if sub:
                return sub
        i = payload_end
    return None

def find_decoder_specific_info(esds):
    # skip version/flags
    i, buf = 0, esds[4:] if len(esds) > 4 else esds
    while i < len(buf):
        tag = buf[i]
        i += 1
        length, i = read_descr_len(buf, i)
        if length is None:
            return None
        payload_start = i
        payload_end = min(i + length, len(buf))
        payload = buf[payload_start:payload_end]
        if tag == 0x05:
            return payload
        # Descend manually into known descriptor payload layouts.
        if tag == 0x03: # ES_DescrTag: ES_ID 2 bytes + flags 1 byte, then child descriptors
            flags = payload[2] if len(payload) >= 3 else 0
            sub_offset = 3
            if flags & 0x80:  # streamDependenceFlag
                sub_offset += 2
            if flags & 0x40:  # URL_Flag
                if sub_offset < len(payload):
                    sub_offset += 1 + payload[sub_offset]
            if flags & 0x20:  # OCRstreamFlag
                sub_offset += 2
            sub = scan_descriptor_block(payload[sub_offset:])
            if sub:
                return sub

        elif tag == 0x04:
            sub = scan_descriptor_block(payload[13:])
            if sub:
                return sub

        i = payload_end
    return None

def find_mdat(data):
    i = 0
    while i + 8 <= len(data):
        size = struct.unpack(">I", data[i:i+4])[0]
        typ = data[i+4:i+8]
        if size == 1:
            if i + 16 > len(data):
                break
            atom_size = struct.unpack(">Q", data[i+8:i+16])[0]
            header = 16
        elif size == 0:
            atom_size = len(data) - i
            header = 8
        else:
            atom_size = size
            header = 8
        if typ == b"mdat":
            return i + header, i + atom_size
        if atom_size < header:
            break
        i += atom_size
    raise RuntimeError("No mdat atom found")

def get_good_header(ref):
    data = open(ref, "rb").read()
    first_vop = data.find(START + b"\xB6") # Keep everything before the first VOP frame start code: 00 00 01 B6
    if first_vop == -1:
        raise RuntimeError("No MPEG-4 VOP start code found in reference.m4v")
    return data[:first_vop]

def extract_length_prefixed_mp4v(mdat):
    out, i, packets  = bytearray(), 0, 0
    while i + 5 <= len(mdat):
        n = struct.unpack(">I", mdat[i:i+4])[0]
        if 8 <= n <= len(mdat) - i - 4:
            pkt = mdat[i+4:i+4+n]
            if (START + b"\xB6") in pkt: # MPEG-4 VOP frames usually contain 00 00 01 B6
                out += pkt
                packets += 1
                i += 4 + n
                continue
        i += 1
    return bytes(out), packets

def extract_working_bin(good_mp4, dst_bin, verbose = True):
    data = open(good_mp4, "rb").read()
    esds = find_esds(data)
    if not esds:
        raise RuntimeError("No esds box found")
    config = find_decoder_specific_info(esds)
    if not config:
        raise RuntimeError("No DecoderSpecificInfo tag 0x05 found")
    if not dst_bin is None:
        open(dst_bin, "wb").write(config)
    if verbose:
        print(f"Wrote {len(config)} bytes to {dst_bin}")
        print("hex:", config[:64].hex())
    
        for pat, name in [
            (b"\x00\x00\x01\xb0", "Visual Object Sequence"),
            (b"\x00\x00\x01\xb5", "Visual Object"),
            (b"\x00\x00\x01\x20", "VOL"),
            (b"\x00\x00\x01\xb6", "VOP"),]:
            print(name, config.find(pat), "count", config.count(pat))
    if dst_bin is None:
        return config

default_mp4_fourcc = 'avc1' if os.name=='nt' else 'mp4v'
def gen_example_mp4(trg_dir='./example_mp4', use_fourcc=default_mp4_fourcc):
    import numpy as np
    import cv2
    import time
    import signal
    
    for pass0 in ['good','bad']:
        trg_path = f"{trg_dir}/{pass0}_{use_fourcc.lower()}.mp4"
        tmp_ims = [np.uint8(np.random.random((128,128,3))*255) for _ in range(128)] #random background
        out_w = cv2.VideoWriter(trg_path,cv2.VideoWriter_fourcc(*use_fourcc), 8, (tmp_ims[0].shape[1],tmp_ims[0].shape[0])) #8fps
        for i in range(len(tmp_ims)):
            tmp_ims[i] = cv2.line(tmp_ims[i],(0,127),(127,0),(255, 255, 255), (128-i)) #contracting white diagonal line
            tmp_ims[i] = cv2.line(tmp_ims[i],(0,0),(127,127),(0, 0, 0), i//4+8) #slowly expanding black diagonal line
            out_w.write(tmp_ims[i])
        if pass0 in 'bad':
            own_id = os.getpid()
            print(f"Killing own process {own_id} on purpose to create broken mp4..")
            time.sleep(2.0) #allow remaining content to be streamed to disk
            #cascade of three ways to abruptly kill own process without cleaning up/closing the open stream 
            if os.name=='nt':
                os.system(f"taskkill /pid {own_id} /f")
            os.kill(os.getpid(), 9)
            sys.exit(1)
        out_w.release() #for "good" path, close the stream properly -> working mp4

def fix_mp4(prepend_bin, broken_mp4, outpath=None, verbose = True):
    if outpath is None:
        outpath = broken_mp4[:-4]+'_fixed.mp4'
    if prepend_bin.endswith('.bin'):
        config_data = open(prepend_bin,'rb').read()
    else:
        config_data = extract_working_bin(prepend_bin, dst_bin=None, verbose=False) #inplace load config
    if START + b"\x20" not in config_data and verbose:
        print("Warning: config does not appear to contain a VOL header: 00 00 01 20")
    
    broken_data = open(broken_mp4,'rb').read()
    start, end = find_mdat(broken_data)
    mdat = broken_data[start:end]
    frames, packets = extract_length_prefixed_mp4v(mdat)
    if packets == 0:
        if verbose:
            print("No length-prefixed MPEG-4 VOP packets found.")
            print("Trying direct start-code extraction instead...")
        first = mdat.find(START + b"\xB6")
        if first == -1:
            raise RuntimeError("No VOP start code found in mdat")
        frames = mdat[first:]
    tmp_path = os.path.dirname(outpath)+'/.tmp_m4v.'+os.path.basename(outpath)
    try:
        open(tmp_path,'wb').write(config_data + frames)
        exec_m4v_to_mp4 = f'ffmpeg -y -f m4v -i {tmp_path} -c:v copy {outpath}'
        exec_m4v_res = os.popen(exec_m4v_to_mp4).read() #only works for valid files
        if not os.path.exists(outpath) and verbose:
            print("ffmpeg seems to fail:", exec_m4v_res)
    except Exception as e:
        print("Recovery failed: ", e)
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    
    