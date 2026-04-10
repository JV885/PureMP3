@echo off
echo Building Android App for JV PureMP3...
echo.
echo Packaging the application into an APK using Flet...
flet build apk --project jv_puremp3 --module-name mp3_downloader_android .
echo.
echo Build complete. The APK will be available in the "build\apk" directory.
pause
