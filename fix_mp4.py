#!/usr/bin/env python3

# attempts to fix broken mp4 written by opencv
# Needs ffmpeg installed/in path and a "good"/successfull mp4 written by the process with the same parameters/width/codec etc.

import os
import sys
import struct
from pathlib import Path

START3 = b"\x00\x00\x01"
ANNEXB = b"\x00\x00\x00\x01"

def check_video_packets(p):
    ret_pack = {}
    if not os.path.exists(p):
        return {}
    data = Path(p).read_bytes()
    for pat,name in [(b"\x00\x00\x01\xb6","VOP"), (b"\x00\x00\x01\x20","VOL"), (b"mdat","mdat")]:
        ret_pack[name.lower()] = {'pos':data.find(pat), 'cnt':data.count(pat)}
    exec_ffprobe='ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,codec_tag_string,profile,width,height,pix_fmt,r_frame_rate,avg_frame_rate,duration,bit_rate -of default=nw=1 '
    ffprobe_res = os.popen(exec_ffprobe+p).read() #only works for valid files
    if len(ffprobe_res) > 0:
        ret_pack.update({kv.split('=')[0].strip().lower():kv.split('=')[1].strip().lower() for kv in ffprobe_res.split('\n') if '=' in kv and len(kv)>3})
    return ret_pack

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

def find_start_atom(data, atom_start='esds'):
    def walk(start, end, path=""):
        for a0, a1, typ, payload in atom_children(data, start, end):
            if typ == atom_start:
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
            elif typ == {"avc1", "avc3", "encv", "mp4v"}:
                # VisualSampleEntry header is 78 bytes
                r = walk(payload + 78, a1, path + "/"+typ)
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

# frm_type   0 = I-VOP, 1 = P-VOP, 2 = B-VOP, 3 = S-VOP
def get_first_vop(mdat, skip_frms=-1, frm_type=0):
    first_vop, pos, num_skipped = -1, 0, 0 # Keep everything before the first VOP frame start code: 00 00 01 B6
    while True:
        pos = mdat.find(START3 + b"\xB6", pos)
        if skip_frms < 0:
            first_vop = pos
        if pos < 0 or pos + 4 >= len(mdat) or skip_frms < 0:
            break
        vop_coding_type = (mdat[pos + 4] >> 6) & 0b11 
        if vop_coding_type == frm_type:
            first_vop = pos
            num_skipped += 1
            if num_skipped >= skip_frms:
                break
        pos += 4
    return first_vop

def is_i_vop(pkt):
    marker = START3 + b"\xB6"
    p = pkt.find(marker)
    if p < 0 or p + 4 >= len(pkt):
        return False
    next_byte = pkt[p + 4]
    vop_coding_type = (next_byte >> 6) & 0b11
    return vop_coding_type == 0

def extract_length_prefixed_mp4v(mdat):
    out, i, num_packets  = bytearray(), 0, 0
    while i + 5 <= len(mdat):
        n = struct.unpack(">I", mdat[i:i+4])[0]
        if 8 <= n <= len(mdat) - i - 4:
            pkt = mdat[i+4:i+4+n]
            if (START3 + b"\xB6") in pkt: # MPEG-4 VOP frames usually contain 00 00 01 B6
                out += pkt
                num_packets += 1
                i += 4 + n
                continue
        i += 1
    return bytes(out), num_packets

def extract_working_bin(data, atom_start='esds', verbose = True):
    start_atom = find_start_atom(data, atom_start)
    if not start_atom:
        return None
    config = find_decoder_specific_info(start_atom)
    if not config:
        raise RuntimeError("No DecoderSpecificInfo tag 0x05 found")
    if verbose:
        print(f"Wrote {len(config)} bytes to {dst_bin}")
        print("hex:", config[:64].hex())
    
        for pat, name in [
            (b"\x00\x00\x01\xb0", "Visual Object Sequence"),
            (b"\x00\x00\x01\xb5", "Visual Object"),
            (b"\x00\x00\x01\x20", "VOL"),
            (b"\x00\x00\x01\xb6", "VOP"),]:
            print(name, config.find(pat), "count", config.count(pat))
    return config

  
def find_boxes(data, target):
    found = []
    def walk(start, end):
        for a0, a1, typ, payload in atom_children(data, start, end):
            if typ == target:
                found.append((a0, a1, payload))
            if typ in {"moov", "trak", "mdia", "minf", "stbl"}:
                walk(payload, a1)
            elif typ == "stsd":
                walk(payload + 8, a1)
            elif typ in {"avc1", "avc3", "encv"}:
                walk(payload + 78, a1)
    walk(0, len(data))
    return found

def find_box_payload(data, target):
    boxes = find_boxes(data, target)
    if not boxes:
        return None
    a0, a1, payload = boxes[0]
    return data[payload:a1]

def parse_avcc(avcc):
    length_size, p = (avcc[4] & 3) + 1, 5
    num_sps, sps = avcc[p] & 0x1F, []
    p += 1
    for _ in range(num_sps):
        n = struct.unpack(">H", avcc[p:p+2])[0]
        p += 2
        sps.append(avcc[p:p+n])
        p += n
    num_pps, pps = avcc[p], []
    p+=1
    for _ in range(num_pps):
        n = struct.unpack(">H", avcc[p:p+2])[0]
        p += 2
        pps.append(avcc[p:p+n])
        p += n
    return length_size, sps, pps

def nal_type(nal):
    return nal[0] & 0x1F if nal else None

def plausible_h264_nal(nal):
    if not nal:
        return False
    if nal[0] & 0x80:
        return False
    return nal_type(nal) in {1, 5, 6, 7, 8, 9, 10, 11, 12}

def parse_avc_payload_to_nals(payload, length_size=4, skip_idr=0, verbose=False):
    nals, p, invalid_start = [], 0, skip_idr >= 0
    while p + length_size <= len(payload):
        n = int.from_bytes(payload[p:p + length_size], "big")
        if n <= 0:
            if verbose:
                print(f"Stop at payload offset {p}: invalid length {n}")
            break
        start = p + length_size
        end = start + n
        if end > len(payload):
            if verbose:
                print(f"Stop at payload offset {p}: length {n} exceeds remaining; {len(payload) - start}")
            break
        nal = payload[start:end]
        if not plausible_h264_nal(nal):
            if verbose:
                print(f"Stop at payload offset {p}: implausible NAL;type={nal_type(nal)}, size={len(nal)}, head={nal[:16].hex()}")
            break
        if invalid_start:
            if nal_type(nal) == 5:
                skip_idr -= 1
            invalid_start = skip_idr >= 0
        if not invalid_start:
            nals.append(nal)
        p = end
    if verbose:
        print(f"Parsed NALs: {len(nals)}")
        print(f"Consumed bytes: {p} / {len(payload)}")

    for i, nal in enumerate(nals[:20]):
        print(f"#{i:04d} type={nal_type(nal):2d} size={len(nal):6d} head={nal[:12].hex()}")

    return nals

# line drawing based on code from Marco Spinaci
# see https://stackoverflow.com/questions/31638651/how-can-i-draw-lines-into-numpy-arrays
def drawline(im0, pt0, pt1, color, thickness=1):
    import numpy as np
    c0, r0, c1, r1  = pt0[0], pt0[1], pt1[0],pt1[1]
    flip_axis = abs(c1-c0) < abs(r1-r0)
    if flip_axis:
        r0, c0, r1, c1  = c0, r0, c1, r1
    if c0 > c1:
        r0, c0, r1, c1  = r1, c1, r0, c0
    slope, x = (r1-r0) / (c1-c0), np.arange(c0, c1+1, dtype=float)
    y = x * slope + (c1*r0-c0*r1) / (c1-c0)
    yy = (np.floor(y).reshape(-1,1) + np.arange(-thickness+1,thickness).reshape(1,-1))
    xx, yy = np.repeat(x, yy.shape[1]), yy.flatten()
    if flip_axis:
        xx, yy = yy, xx
    mask = np.logical_and.reduce((yy >= 0, yy < im0.shape[0], xx >= 0, xx < im0.shape[1]))
    im0[(yy[mask].astype(int), xx[mask].astype(int))] = color

default_mp4_fourcc = 'avc1' if os.name=='nt' else 'mp4v'
# gen_example_mp4: method to create a working and a broken mp4 container; 
# use_libav switches between opencv and libav video writer
def gen_example_mp4(trg_dir='./example_mp4', use_fourcc=default_mp4_fourcc, use_libav=False):
    import numpy as np
    import time
    import signal
    os.makedirs(trg_dir, exist_ok=True)
    encoder='ocv'
    if use_libav:
        trg_encoder, encoder = use_fourcc if use_fourcc!= default_mp4_fourcc else 'libx264', 'libav'
    for pass0 in ['good','bad']:
        trg_path = f"{trg_dir}/{pass0}_{encoder}_{use_fourcc.lower()}.mp4"
        tmp_ims = [np.uint8(np.random.random((128,128,3))*255) for _ in range(128)] #random background
        if use_libav:
            import av
            out_w = av.open(str(trg_path), mode="w")
            out_w_stream = out_w.add_stream(trg_encoder, 8, options={'crf':'21'})
            out_w_stream.width, out_w_stream.height = tmp_ims[0].shape[1], tmp_ims[0].shape[0]
        else:
            import cv2
            out_w = cv2.VideoWriter(trg_path,cv2.VideoWriter_fourcc(*use_fourcc), 4, (tmp_ims[0].shape[1],tmp_ims[0].shape[0])) #8fps
        for i in range(len(tmp_ims)):
            drawline(tmp_ims[i],(0,127),(127,0),(255, 255, 255), (128-i)) #contracting white diagonal line
            drawline(tmp_ims[i],(0,0),(127,127),(0, 0, 0), i//4+8) #slowly expanding black diagonal line
            if use_libav:
                frame = av.VideoFrame.from_ndarray(tmp_ims[i], format="bgr24")
                for packet in out_w_stream.encode(frame):
                    out_w.mux(packet)
            else:
                out_w.write(tmp_ims[i])
        if use_libav: #empty stream
            for packet in out_w_stream.encode():
                out_w.mux(packet)
        if pass0 in 'bad':
            own_id = os.getpid()
            print(f"Killing own process {own_id} on purpose to create broken mp4..")
            time.sleep(2.0) #allow remaining content to be streamed to disk
            #cascade of three ways to abruptly kill own process without cleaning up/closing the open stream 
            if os.name=='nt':
                os.system(f"taskkill /pid {own_id} /f")
            os.kill(os.getpid(), 9)
            sys.exit(1)
        #for "good" path, close the stream properly -> working mp4
        if use_libav:
            out_w.close()
        else:
            out_w.release() 
  
# ffmpeg_fix: method to add mp4 container to pure m4v data part; possible are 'direct', 'reencode' and 'none'
def fix_mp4(prepend_bin, broken_mp4, outpath=None, verbose = True, ffmpeg_op='direct', atom_start='esds', skip_keyfrms=0):
    extract_bin = str(broken_mp4).lower() == 'none'
    if str(outpath).lower() == 'none':
        outpath = prepend_bin[:-4]+'_extr.bin' if extract_bin else broken_mp4[:-4]+'_fixed.mp4'
    config_data = open(prepend_bin,'rb').read()
    if not prepend_bin.endswith('.bin'):
        try:
            config_data_extr = extract_working_bin(config_data, atom_start=atom_start, verbose=extract_bin) #inplace load config
            if START3 + b"\x20" not in config_data_extr and verbose:
                print("Warning: config does not appear to contain a VOL header: 00 00 01 20")
        except:
            config_data_extr = None
        if config_data_extr is None and atom_start == "esds": #Assuming in-band SPS/PPS files
            avcc = find_box_payload(config_data, "avcC")
            length_size, sps_list, pps_list = parse_avcc(avcc)
            if len(sps_list) < 1 or len(pps_list) < 1:
                raise RuntimeError(f"Neither "+atom_start+" atom nor in-band SPS/PPS in reference file found")
            config_data, inband_sps = bytearray(), True
            for sps in sps_list:
                config_data += ANNEXB + sps
            for pps in pps_list:
                config_data += ANNEXB + pps
        else:
            config_data = config_data_extr
    else:
        inband_sps = atom_start not in config_data
    if extract_bin: #only extract good part for recovery in other calls
        open(outpath,'wb').write(config_data)
        return
    broken_data = open(broken_mp4,'rb').read()
    start, end = find_mdat(broken_data)
    mdat, num_packets = broken_data[start:end], 0
    print("mdat-offs",start,end)
    if inband_sps:
        best_nals = []
        for init_offs in [0,4,8]:
            nals = parse_avc_payload_to_nals(mdat[init_offs:], length_size=length_size, skip_idr=skip_keyfrms, verbose=verbose)
            if len(nals) > len(best_nals):
                best_nals = nals
            if verbose:
                print(f"Found {len(nals)} valid NALS at {init_offs} for in-band SPS/PPS file.")
        frames = bytearray()
        for nal in best_nals:
            frames += ANNEXB + nal
    else:
        if skip_keyfrms < 0:
            frames, num_packets = extract_length_prefixed_mp4v(mdat)
            
        if num_packets == 0:
            if verbose and skip_keyfrms < 0:
                print("No length-prefixed MPEG-4 VOP packets found.")
                print("Trying direct start-code extraction instead...")
            first_ivop = get_first_vop(mdat, skip_keyfrms)
            frames = mdat[first_ivop:]
    ret_val, exec_m4v_to_mp4 = -1, None
    trg_tmp_type = 'm4v' if atom_start == 'esds' and not inband_sps else 'h264 -fflags +genpts'
    tmp_path = os.path.dirname(outpath)+'/.tmp_.'+os.path.basename(outpath)[:-3]+trg_tmp_type.split(' ')[0]
    try:
        open(tmp_path,'wb').write(config_data + frames)
        if ffmpeg_op == 'none':
            os.rename(tmp_path,outpath)
        elif ffmpeg_op == 'direct': # -fflags +genpts 
            exec_m4v_to_mp4 = f'ffmpeg -y -f {trg_tmp_type} -i {tmp_path} -c:v copy {outpath} 2>&1'
        else:
            exec_m4v_to_mp4 = f'ffmpeg -y -f {trg_tmp_type} -i {tmp_path} -c:v mpeg4 -q:v 2 {outpath} 2>&1'
        if not exec_m4v_to_mp4 is None:
            exec_m4v_res = os.popen(exec_m4v_to_mp4).read() #only works for valid files
            if not os.path.exists(outpath) and verbose:
                print("ffmpeg seems to fail:", exec_m4v_res)
        res_f = check_video_packets(outpath)
        res_sec = float(res_f.get('duration', -1.0))
        if res_sec > 1.0:
            if verbose:
                print(f"Success, duration: {res_sec} sec at {outpath}")
            ret_val = 0
        elif verbose:
            print("Fixing failed; result:", res_f)
            print("Old was: ", check_video_packets(broken_mp4))
    except Exception as e:
        print("Recovery failed: ", e)
        ret_val = -2

    #if os.path.exists(tmp_path):
    #    os.remove(tmp_path)
    return ret_val
    
    