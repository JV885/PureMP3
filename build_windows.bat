@echo off
echo Building Windows Executable for JV PureMP3...
echo.
echo Packaging the application into a single executable, embedding assets and ffmpeg...
pyinstaller --noconfirm --onefile --windowed --name "jv_puremp3" --icon "asset/icon.ico" --add-data "asset;asset" --add-data "ffmpeg.exe;." --add-data "ffprobe.exe;." "jv-puremp3.py"
echo.
echo Build complete! You can find jv_puremp3.exe in the "dist" folder.
pause
