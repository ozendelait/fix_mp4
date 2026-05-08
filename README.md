## Tool for Fixing Broken mp4 Files

This tool is intended to fix broken mp4 files, especially mp4 files with H.264 encoding that were recorded using OpenCV or libav, where the recording process terminated unexpectedly and the file writer stream was not properly closed or released.

This results in a broken mp4 file even though 99% of the data is still preserved. This tool attempts to recover such files, but it relies on a second "good" example mp4 file from which metadata can be copied.

The "good" file should originate from the same recording session (same process, same codec, same parameters, same image dimensions, etc.).

### Requirements

A working Python 3.x environment and a reasonably recent version of FFmpeg available in `PATH`.

OpenCV and/or libav are only required when generating test videos, not for running the tool itself.

### Usage

```text
usage: fix_mp4.py [-h] [--input INPUT] [--template TEMPLATE] [--output OUTPUT]
                  [--ffmpeg_op FFMPEG_OP] [--atom_start ATOM_START]
                  [--skip_keyfrms SKIP_KEYFRMS] [--verbose]

Params

options:
  -h, --help            show this help message and exit
  --input INPUT, -i INPUT
                        Broken mp4 file to be fixed; if omitted,
                        the extracted template will be saved as <output>
  --template TEMPLATE, -t TEMPLATE
                        Working "good" mp4 file used as a template, or a
                        previously extracted template.bin file
  --output OUTPUT, -o OUTPUT
                        Target path for the recovered mp4 file; default:
                        <input>_fixed.mp4
  --ffmpeg_op FFMPEG_OP, -f FFMPEG_OP
                        Final FFmpeg operation required for mp4 container
                        creation; "direct" for direct copying or "reencode"
                        for re-encoding
  --atom_start ATOM_START, -a ATOM_START
                        Start atom tag name expected in the template file
                        for extracting stream metadata
  --skip_keyfrms SKIP_KEYFRMS, -s SKIP_KEYFRMS
                        Drop this many keyframes at the beginning;
                        0 -> drop everything before the first keyframe,
                        -1 -> drop nothing
  --verbose, -v         Display process information useful for debugging
```

### Note on Installing PyAV/libav Python Bindings

Installing the Python bindings for libav is not required to run the tool itself.

For debugging and testing, example mp4 files can be generated, which requires libav. 
The Python bindings can sometimes be difficult to install. The following usually works:

```bash
python -m pip install --upgrade pip
python -m pip install "av" --only-binary=:all:
```
