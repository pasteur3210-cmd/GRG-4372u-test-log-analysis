# GRG4372 MP Analyzer V1.2.0 (GitHub Actions)

以輸入 ZIP 的 **LOG** 重新計算全部可解析項目，CSV 僅作參考計數與核對，不取代 LOG 限值或判定。

## GitHub Actions（Windows EXE）
1. 建立 **Private** repository，將本 ZIP 解壓後的內容（含 `.github/workflows/build.yml`）上傳 repository 根目錄。
2. `Actions > Build GRG4372 MP Analyzer > Run workflow` 或 push 到 `main`。
3. Build 成功後下載 `GRG4372_MP_Analyzer_V1.2.0` artifact，解壓取得 EXE。
4. Windows 11 執行 EXE，選擇 MP Log ZIP 與報告輸出路徑，點選開始。
5. 報告旁 `Records/` 產生 execution.log/test.log/debug.log。

命令列開發驗證：`python main.py --input "達天MP test log.zip" --output report.xlsx`。
Python 3.11+；正式 EXE 編譯使用 GitHub Actions / PyInstaller。 

## 已實作
- 根據 LOG 內容辨識站別，路徑名稱為備援。
- RF 每筆 LOG 擷取 Low / Test Value / High / PASS/FAIL；CSV 不覆寫正式資料。
- RF PCB Seq ID 修正，排除 RF 規格 group key 覆蓋識別碼。
- MES-only 擋站不計入功能 FAIL、有效功能測試分母或功能重測；MES 另列事件/機台。
- 功能首測不良率、末測不良率、功能重測率、FAIL 重測成功率。
- 最終 FAIL → FAIL轉PASS → 其他重測 → MES → 首測 PASS 排序。
- 32工作表版型，00 sheet `veryHidden`；七站工時和異常追蹤。

## 目前尚未滿足正式核准版逐格一致驗收
- 16A 測試前間隔、26 耦合站內覆測、27 流量規格欄位、29 流量缺測等細部工作表，尚未完成與核准版等價的 LOG parser。
- 原版 3,662 的統計口徑與本版 3,653 有效 LOG 事件尚待對帳。此值不等於目前程式漏掉 9 筆；需先核對事件定義。
- 目前未在 GitHub Windows runner 實際建置 EXE。應以 Actions 成功、Windows 實際執行為最後驗收。
- LOG 缺乏識別碼的 7 個耦合 FAIL 事件仍出現在品質問題追蹤，不得自動歸屬到任何機台。

## 資料保護
模板已清空舊製令/生產資料（僅留格式/標題），GitHub 不應上傳原始 MP ZIP 或分析報告。建議 private repo。

更多資訊見 SOP.md / VERIFICATION.md。
