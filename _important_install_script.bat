@echo off
set "CHATTERBOX=chatterbox"
set "VENV_DIR=venv"

set "TARGET_DIR=%userprofile%\chatterbox\test\target"

if not exist %VENV_DIR% (
	py -3.10 -m venv %VENV_DIR%
	%VENV_DIR%\Scripts\pip install -e .
	REM Detect GPU architecture
	nvidia-smi --query-gpu=name --format=csv,noheader > gpu_name.tmp
	findstr /C:"RTX 50" gpu_name.tmp >nul
	if %errorlevel%==0 (
		echo Detected RTX 50 series - Installing PyTorch with CUDA 12.8
		%VENV_DIR%\scripts\pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
	) else (
		echo Installing PyTorch with CUDA 12.6
		%VENV_DIR%\scripts\pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu126
	)
	del gpu_name.tmp
	%VENV_DIR%\Scripts\pip install gradio
)

if not exist %TARGET_DIR% (
	mkdir %TARGET_DIR%
)

echo Please run _important_create_shortcut.bat to create a desktop shortcut for Chatterbox.
:: check if ffmpeg is installed
where ffmpeg >nul 2>nul
if %errorlevel% neq 0 (
    echo ffmpeg is not installed. Please install ffmpeg and ensure it is in your PATH. FROM https://github.com/BtbN/FFmpeg-Builds/releases
    pause
    exit /b 1
)
