$ErrorActionPreference = "Stop"

function Ensure-Dir($path) {
  if (-not (Test-Path $path)) {
    New-Item -ItemType Directory -Path $path | Out-Null
  }
}

Write-Host "[1/8] Ensure Python 3.10 virtual environment"
if (-not (Test-Path ".venv")) {
  py -3.10 -m venv .venv
}

Write-Host "[2/8] Upgrade pip tooling"
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel

Write-Host "[3/8] Install backend dependencies"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host "[3.5/8] Install PyTorch CUDA runtime for SenseVoice"
.\.venv\Scripts\python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124

Write-Host "[4/8] Ensure .env exists"
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
}

Write-Host "[5/8] Ensure local directories"
Ensure-Dir "models"
Ensure-Dir "models\piper"
Ensure-Dir "bin"
Ensure-Dir "bin\piper"

Write-Host "[6/8] Download SenseVoiceSmall (ModelScope iic/SenseVoiceSmall)"
.\.venv\Scripts\python.exe -c "from modelscope import snapshot_download; import os, shutil; src=snapshot_download('iic/SenseVoiceSmall'); dst=os.path.abspath('models/SenseVoiceSmall'); shutil.rmtree(dst, ignore_errors=True); shutil.copytree(src, dst); print('SenseVoiceSmall ->', dst)"

Write-Host "[7/8] Download Piper runtime and voice"
$zipPath = "bin\piper\piper_windows_amd64.zip"
Invoke-WebRequest -Uri "https://github.com/rhasspy/piper/releases/latest/download/piper_windows_amd64.zip" -OutFile $zipPath
Expand-Archive -Path $zipPath -DestinationPath "bin\piper" -Force
Remove-Item $zipPath -Force

Invoke-WebRequest -Uri "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx" -OutFile "models\piper\en_US-amy-medium.onnx"
Invoke-WebRequest -Uri "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json" -OutFile "models\piper\en_US-amy-medium.onnx.json"

Write-Host "[8/8] Verify Ollama and pull Gemma model"
$ollamaVersion = ollama --version
Write-Host $ollamaVersion
ollama pull gemma4:e2b

Write-Host "Setup complete."
Write-Host "Start backend with: .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
