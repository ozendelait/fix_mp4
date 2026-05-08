## Tool for fixing broken mp4 files

This tool is intended to fix broken mp4 files, especially mp4 files with h264 encoding which were recorded using opencv or libav where the recording process terminated unexpectedly and the file writer stream was not closed/released. This results in a broken mp4 files although 99% of all data is preserved. This tool tries to fix these files but relies on a second "good" example mp4 to copy meta data. This "good" file should be from the same recording session (same process, same codec, same parameters, same image dimensions, etc.).

### Requirements:
Working python 3.x environment and fairly current version of ffmpeg in PATH. Opencv and/or libav are only needed when generate test videos, not for the tool itself.
