# GRG-4372u MP Log 差異分析程式 SOP（V1.2.0）

## 1 目的、使用範圍
將新批次工廠 MP LOG 壓縮為 ZIP，自動產生七站測試、複測、MES、RF 規格及工時 Excel。輸出 32 張工作表。
**LOG 是唯一正式量測/結果來源；CSV 只作輔助核對。** 新版仍有部分專項工作表待完善，不能單憑本報告自動放行批次。

## 2 GitHub Actions 建置
1. 將 `GRG4372_MP_Analyzer_V1.2.0_GitHub.zip` 解壓。
2. 在 GitHub Private repo 根目錄上傳 `main.py`、`analyzer.py`、`legacy_audit.py`、`xlsx_template_writer.py`、`schema.json`、`rules.json`、`templates/`、`tests/`、`.github/workflows/build.yml`、`requirements.txt` 等。
3. GitHub → Actions → Build GRG4372 MP Analyzer → Run workflow。
4. Job 先執行 Python 語法檢查、pytest，再執行 PyInstaller onefile/GUI 建置。
5. 從 Action Run 頁面下方 Artifacts 下載 Windows EXE。

## 3 工廠操作
1. Windows 11 解壓縮 GitHub Artifact，執行 `GRG4372_MP_Analyzer.exe`。
2. 選擇當批 `MP test log.zip`。不必依據資料夾名稱強制分站。
3. 選擇 Excel 檔案輸出路徑；按「開始分析並產出報告」。
4. 程式完成後開啟 Excel，檢查 `01_各站統計` / `02_逐台最終狀態`。
5. FAIL 與複測優先在明細上方。MES 重複過站獨立計數且不計入功能不良率。
6. 檢查 `18_資料品質問題`；缺失識別碼、限值異常等必須處理。
7. `Records/execution.log`、`Records/test.log`、`Records/debug.log` 必須保存供稽核。

## 4 指標定義
- 首測功能不良率 = 首筆有效功能 FAIL/ABORT 唯一機台數 / 有效功能測試唯一機台數 × 100%。
- 最終功能不良率 = 最後有效功能 FAIL/ABORT 唯一機台數 / 有效功能測試唯一機台數 × 100%。
- 重測率 = 至少兩筆有效功能 LOG 的唯一機台數 / 有效功能測試唯一機台數 × 100%。PASS→PASS 要計重測，不要計不良。
- FAIL 重測成功率 = 首測功能 FAIL 且有重測後最終 PASS 台數 / 首測功能 FAIL 且有重測台數 × 100%。
- MES 擋站：獨立事件/機台數；MES-only 不列入功能率值分子或分母。
- RF：同次 LOG 每筆測量值、上下限及 PASS/FAIL；CSV 不能覆寫。RF TotalTime 為實測；BOB 相鄰 PASS 間隔只是推估節拍。

## 5 品質驗收與限制
- 核對 32 張工作表、00 分頁 `VeryHidden`、FAIL/複測優先及各站的 4 種率值。
- 對 RF LOG 隨機抽查 Low/Value/High/Status、PCB Seq 與單次 TotalTime。
- MES 擋站機台核對原 LOG 事件和獨立清單，不得算功能 FAIL。
- 目前已通過 10/10 本機自動測試及當批 ZIP 實跑；**尚未逐格達到 V2.6.1 完全等價**，16A、26、27、29 深入分析有待完善。
- GitHub Windows 編譯和 Windows EXE 實際啟動仍需上線驗收；不得以本機 pytest 代替 EXE 驗收。

## 6 異常處理
- 無識別碼：查看 `18_資料品質問題`，勿猜測 MAC/PCB。
- RF 規格衝突：查看 `21_`/`23_`/`22_`，以當批 LOG 核對。
- 無 PASS 工時：查該 LOG 是否有可靠 TotalTime，不能用相鄰 LOG 硬代替實測時間。
- 統計與核准版不同：先核對 MES 排除口徑、有效功能 LOG 定義與重測排序；保留兩版本與差異紀錄。
