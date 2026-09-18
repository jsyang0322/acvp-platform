# ACVP Platform — 後量子密碼驗證伺服器（FIPS 203 / 204）

**Language / 語言：[English](README.md) ｜ 繁體中文**

密碼模組要取得聯邦認證，必須由驗證機構實際測過，而不是只交文件。**ACVP**（NIST 的自動化密碼驗證
協定）就是這個測試在網路上進行的方式：伺服器發出測試向量，待測實作（IUT）算出答案，伺服器再用實作
看不到的標準答案去批改。

這個專案就是那台伺服器，加上驅動它的網頁 client，對象是兩項新的**後量子**標準：**FIPS 203
（ML-KEM）** 與 **FIPS 204（ML-DSA）**。整套依 [`draft-fussell-acvp-spec`](https://pages.nist.gov/ACVP/)
從協定層自己寫起：`acvVersion` 訊息信封、JWT 簽發與 per-session 授權範圍、vectorSet 狀態機、兩個
各自獨立的非同步輪詢點、七狀態 disposition 模型，以及 mTLS 終結點，都是依規格實作，不是改一套現成
的 ACVP server。

密碼運算本身放在行程邊界後面。伺服器以檔案式介面驅動 NIST 自己的 ACVP-Server GenVal 引擎，JSON 進、
JSON 出，而不是重寫一套 ML-KEM 與 ML-DSA：向量的可信度取決於產生它的實作，而對標準化不久的演算法而言，
可信的來源即為參考實作。這條邊界也決定了架構，協定層底下可以接釘版的 NIST 黃金向量，也可以
接真的引擎，上層不需要知道現在接的是哪一個。

> 本專案是 FIPS 203 / 204 驗證平台的協定層與網頁 client。
> `docs/` 底下的設計紀錄以繁體中文撰寫；英文入口是 [README.md](README.md)。

---

## 🔬 證據

以下各項均可獨立查核：

| 證據 | 結果 | 報告 |
|---|---|---|
| **以 NIST 黃金向量當 oracle** | 五個 ML-KEM / ML-DSA mode 全部從 `usnistgov/ACVP-Server` **唯讀**取入，釘在 commit `15c0f3de`。以 NIST `prompt` 為輸入即須重現 NIST `expectedResults`；不因測試未通過而修改 fixture | [`tests/fixtures/nist/SOURCE.md`](tests/fixtures/nist/SOURCE.md) |
| **測試套件** | 33 個檔案、**200 passed, 6 skipped**。均採測試先行：先讀取黃金向量，撰寫失敗測試，再實作至通過 | [`docs/acvp-conformance.md`](docs/acvp-conformance.md) |
| **逐條規格對照表** | 每項需求均對應其規格條號、規範層級（SHALL / MUST / SHOULD）及據以驗證的測試，並含刻意延後之項目與理由 | [`docs/acvp-conformance.md`](docs/acvp-conformance.md) |
| **CI 上的真 mTLS 握手** | GitHub Actions 對**實際的 nginx 容器**執行完整驗證：憑證拒絕、CRL 撤銷後重載、繞過 proxy 直連後端，而非僅測試中介層本身 | [`.github/workflows/mtls-verify.yml`](.github/workflows/mtls-verify.yml) |
| **瀏覽器端到端** | 以 Playwright 驅動**系統 Chrome** 完整執行六步驟流程並逐步擷圖；任一步驟失敗即以非零狀態結束並保留錯誤截圖 | [`frontend/e2e/`](frontend/e2e/README.md) |
| **對真引擎的驗收** | 一組 opt-in 測試使五個 mode 均經 NIST 實際的 GenVal 引擎執行，所用為**該次產生的隨機向量**而非靜態 fixture：該次產生對應的正確答案須批改為 `passed`，保持格式的竄改則須批改為 `failed` | [`test_nist_real_engine.py`](backend/tests/test_nist_real_engine.py) |
| **批改引擎已釘版** | 判決由引擎產生，故與向量同樣採版本釘選；build 腳本於 off-pin 時**拒絕建置**，除非明確覆寫，以免判決隨引擎無聲漂移 | [`scripts/nist/build-genval.sh`](scripts/nist/build-genval.sh) |

---

## 🏛️ 架構與技術棧

* **Server：** FastAPI + Python 3.11、Pydantic v2。每則 ACVP 訊息均為型別化模型（registration / prompt / response / validation），schema 驗證由型別系統承擔，而非手寫檢查。PyJWT（HS256）、arq 承擔耗時的 generate / validate。
* **儲存：** 預設為記憶體 store，整條流程無須資料庫即可執行 · PostgreSQL 16 採 SQLAlchemy 2 + psycopg3 + Alembic，由 `DATABASE_URL` 決定，且為 write-through，對各端點而言切換透明。
* **網頁 client：** React 18 + Vite 5 + TypeScript，TanStack Query 驅動兩個輪詢點，介面為對應協定流程的六步驟精靈。型別由 FastAPI 的 OpenAPI schema 產生，前後端共用同一份定義，避免兩份定義逐漸分歧。
* **傳輸：** nginx 擔任規格中的 **ACV Proxy**，負責 TLS 1.2 / 1.3 終結、client 憑證驗證與 CRL 撤銷。應用伺服器不對 host 開放任何埠口。
* **密碼邊界：** NIST 的 ACVP-Server GenVal 引擎（C# / .NET 8 + Orleans），以檔案式 CLI 驅動。兩個 provider 實作同一個介面，由 `USE_NIST_GENVAL` 選擇。
* **驗證：** 以 pytest 對黃金向量測試、vitest 測試 client、Playwright 執行瀏覽器流程、GitHub Actions 驗證實際 TLS 握手。

所有協定端點都在 `/acvp/v1/` 底下；`/health` 是唯一免 mTLS 的路徑，供編排器在未持 client 憑證的情況下探測存活：

```
/acvp/v1/   login · login/refresh · algorithms[/{id}]
            testSessions[/{id}]                             建立 · 查詢 · certify · 取消
            testSessions/{id}/results                       session 層總表
            testSessions/{id}/vectorSets[/{vsId}]           取考卷（非同步：{vsId, retry:N}）
            testSessions/{id}/vectorSets/{vsId}/results     POST 交卷 · PUT 重送 · GET 拉成績
            testSessions/{id}/vectorSets/{vsId}/expected    僅 sample session
            requests[/{id}]                                 非同步 request-retry 輪詢
            validations/{id}                                已簽發的驗證紀錄
            vendors · persons · modules · oes · dependencies
/health     liveness（免 mTLS）· /health/db readiness
```

---

## 🧠 工程筆記

實作過程中較具難度的幾項問題與處理方式：

**向量取得與結果取得為兩個獨立的輪詢點。** 結果取得較為直觀，即送出答案後輪詢成績；較易忽略者為
向量取得亦須輪詢。向量尚未產生完成時，伺服器回覆 `{"vsId": N, "retry": 30}`，client 須於 N 秒後
重新請求。二者的狀態與失敗模式均不相同，僅實作其一者，將在產生作業延遲時停止推進。

**送出答案僅回覆 HTTP 狀態碼。** `POST .../results` 不含任何成績資訊，disposition 須另對同一 URL
發出 `GET` 取得。規格如此設計，係因結果由 client 主動取得而非由伺服器推送：驗證機構依自身排程批改，
client 亦可能長時間離線。

**取消須優先於執行中的背景作業。** generate 與 validate 於背景執行緒執行，期間 client 仍持續與伺服器
互動，故可能出現執行緒尚未結束，而 vectorSet 已遭取消或已逾期的情形。若作業完成時逕行寫入狀態，該
寫入將落於取消之後，使已取消的 vectorSet 重新出現於 session 清單，為規格所禁止。因此完成時一律經由
`settle()` 寫入，已進入終態者拒絕寫入；逾期判定亦不以背景作業掃描，改為讀取當下依時鐘計算。

**mTLS 的有效性取決於是否存在其他可達路徑。** nginx 完成 client 憑證驗證後，將結果置於 header 轉送
後端。若後端可被直接連線，該 header 即可由外部偽造。故後端不對 host 開放埠口，另由 nginx 注入共享
密鑰，中介層先行驗證該密鑰，再決定是否採信憑證 header。

**瀏覽器將 client 憑證視為 credentials。** 依 fetch 規格，TLS client 憑證屬於 credential，請求未帶
`credentials: "include"` 時，瀏覽器將逕行丟棄回應；然啟用該模式後，CORS 即不得使用萬用字元 origin。
此外 CORS 中介層須置於 mTLS 中介層之外，preflight 與遭拒回應方能帶有 CORS header。三項限制彼此
連動，且所呈現的症狀相同。

**設定不正確時應拒絕啟動。** JWT 簽章金鑰若仍為佔位值，所有 token 均可被偽造；未設定 proxy 密鑰時，
mTLS 檢查可被繞過。二者均不以警告處理，而由啟動驗證直接阻擋，使失敗模式為服務無法啟動，而非以不安全
的設定正常運行。

**實際合約與文件所述不同。** 初期假設整合方式為 stdin/stdout 命令列工具，實際檢視引擎原始碼後確認為
檔案式 CLI，且 NIST 的 validate 取用 `internalProjection`（完整標準答案）而非 prompt。此差異影響
資料模型：標準答案須於出題當下保存，否則批改階段已無法取得。

---

## 🔒 協定覆蓋範圍

| 能力 | 實作內容 | 規格 |
|---|---|---|
| 訊息信封 | 每則訊息均為 `[{"acvVersion":"1.0"}, {payload}]`；版本不符、缺信封、多出頂層元素一律拒絕；全程採 lowerCamelCase | §10 · §21 |
| 認證 | JWT HS256 採 allow-list（拒絕 `alg:none`）；`iss`/`nbf`/`exp`/`iat` **均實際驗證**而非僅要求存在；強制過期、renewal，以及保序的 Multi-Refresh | §12.3 |
| Session 授權 | per-session `accessToken` 之範圍**僅限該 session**，僅儲存單向雜湊，且僅於簽發當下揭露一次 | §12.16 |
| Test session | 由能力 registration 建立，並提供列表（分頁、依擁有者範圍）、查詢、certify 與取消；`isSample` session 得取得標準答案，正式 session 則否 | §12.16 · §12.17.5.1 |
| Vector set | 具 `retry` 的非同步取卷、含 `expired` 終態的交卷期限，以及可覆蓋執行中 generate / validate 的取消機制 | §12.17 · §14 |
| 結果與 disposition | 七狀態均支援：`passed` / `failed` / `incomplete` / `unreceived` / `missing` / `expired` / `error`；引擎未提供者由生命週期狀態推導，失敗後得整組重送 | `vectorSet_results_*` |
| Request-retry | 耗時作業於背景執行；`GET /requests/{id}` 輪詢 `initial` → `processing` → `approved` / `rejected`，完成後提供 `approvedUrl` | §12.7 |
| Metadata | vendors · persons · modules · oes · dependencies，建立與更新均循同一審核流程，所有傳入的 URL reference 均先驗證再儲存 | §12.8–12.13 |
| 傳輸安全 | 含 CRL 撤銷的 mTLS、TLS 1.2 / 1.3 限用 FIPS 核可的 AES-GCM 密碼組，以及用於阻擋直連後端的共享 proxy 密鑰 | §6 · §7.1 · SP 800-52r2 |
| 第二因子 | 依 NIST credentials 規格實作之 TOTP：RFC 6238、HMAC-SHA-256、8 碼、30 秒步長，seed 以 client 憑證 DN 索引 | RFC 6238 · RFC 4226 |

---

## 🚀 執行方式

**前置需求：** Python 3.11+ 與 Node 20+；完整環境需 Docker；僅在使用實際批改引擎時需要 .NET 8。

```bash
# 取黃金向量（釘版 sparse checkout，寫入後設為唯讀）
scripts/fetch-nist-fixtures.sh

# 起 server。走 fixture provider：不需要 .NET、不需要資料庫、不需要憑證
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                               # 200 passed, 6 skipped
uvicorn app.main:app --reload           # API 在 :8000，OpenAPI 在 /docs

# 另一個終端機起網頁 client。登入密碼：acvp-demo
cd frontend && npm install && npm run dev        # http://localhost:5173
```

完整環境（mTLS、PostgreSQL、實際批改引擎）於產生開發用 PKI 後，以單一指令啟動：

```bash
scripts/gen-certs.sh                    # CA、server、client、CRL、瀏覽器 p12
cp .env.example .env                    # PROXY_SECRET 與 JWT_SECRET；沒填 compose 會拒絕啟動
docker compose up --build               # 網頁 client :5173 · API https://localhost:8443/acvp/v1
```

---

## 📚 範圍與邊界

**密碼運算採整合而非自行實作。** ML-KEM / ML-DSA 的金鑰產生、封裝、簽章、驗章，以及判定通過與否的
比對，均由 NIST 參考實作提供，經行程邊界呼叫，不以函式庫形式引入。後量子演算法標準化時程尚短，自行
實作將降低本平台所發出判決的可信度，故不採用。邊界依此原則劃定：引擎使用的語言與 runtime 對協定層
不可見，此設計亦使同一套協定層得以在黃金向量與實際引擎之間切換。

**已知限制。** ML-KEM 的 encapsulation 與 keyCheck 無法經由內建 .NET API 注入隨機數 `m`，以該 API
實作之待測實作，在此方向的 KAT 覆蓋受限；本平台的向量產生不受影響，其來源為 NIST 參考實作。另，
預設 fixture provider 回傳既有的 validation 內容，可在無 .NET 環境下完整執行協定流程，惟實際批改
仍須接上引擎。
