## 快速遠端啟動指南

### 目標
- 在另一台裝置（桌面、筆電、VM 或遠端主機）準備並執行本專案的自動化 bot (`bot_script.py`)，或進行模型訓練/評估。

---

### 前置條件
- Python 3.8+（建議使用與開發機相同的 minor 版本）
- Git（若從 repo clone）
- 網路可存取 `https://barricade.gg/local`（企業網路請確保防火牆放行）
- 若要觀察瀏覽器 UI：遠端機器需有顯示/桌面環境（RDP/VNC）或在本機執行；否則使用 headless 模式

---

### Windows (PowerShell) — 最短流程
1. 取得專案
```powershell
cd %USERPROFILE%\Desktop
git clone <your-repo-url> BarricadeGG_bot-main
cd BarricadeGG_bot-main
```
2. 建立並啟用 Python 環境（conda 範例）
```powershell
conda create -n barricade python=3.10 -y
conda activate barricade
```
或 venv：
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
3. 安裝依賴
```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```
4. 安裝 Playwright 瀏覽器二進位
```powershell
python -m playwright install chromium
```
5. 放置模型（示例）
把模型檔 `quoridor_ppo_final.zip` 或其他 `.zip` 檔放到 `models/` 目錄。
6. 執行 bot_script：
```powershell
python bot_script.py
```
若要指定模型：
```powershell
python bot_script.py --model-name quoridor_ppo_100000_steps --headless
```
或執行資料夾內最新模型：
```powershell
python bot_script.py --model-name quoridor_ppo_final
```
7. 執行 testbot：
```powershell
python testbot.py
```
如果遠端無 GUI，也可加上 `--headless`：
```powershell
python testbot.py --headless
```

---

### Linux / macOS (bash)
```bash
git clone <your-repo-url> BarricadeGG_bot-main
cd BarricadeGG_bot-main
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python bot_script.py --model-name quoridor_ppo_final
```

無頭環境建議：使用 `xvfb-run` 或 `xvfb` 虛擬顯示（若頁面在 headless 與 headful 行為不同）

---

### 若需訓練模型
```bash
python scripts/train.py --timesteps 100000
```
（可依 `scripts/train.py` 支援的參數調整）

---

### 文件與常見命令
- 專案快速檢視: `README.md`, `QUICK_START.md`
- 日誌: `logs/` (TensorBoard)
- 模型: `models/`

### 常見問題與排查
- Playwright 無法啟動: 確認在相同 Python 環境執行 `python -m playwright install`，並檢查 PATH/權限
- 找不到模型檔: 確認 `--model-name` 或 `--model-dir` 參數正確，或把檔放到 `models/`
- 頁面元素定位失敗: 增加 `wait_for_selector` timeout 或切換到可視模式觀察 DOM
- 無網路或被防火牆封鎖: 確認遠端機器可連上 barricade.gg

---

### 備註與進階選項
- 建議在遠端建立 `.env` 或接受命令列參數：`--model-path`, `--url`, `--headless`, `--log-file`
- 若要在服務器/CI 上排程運行，建議把腳本改成能輸出 JSON 格式日誌並加上重試機制
- 為重現性，建議執行 `pip freeze > requirements_freeze.txt`

---

需要我把 `bot_script.py` 改成支援 `--headless` / `--url` / `--log-file` 嗎？