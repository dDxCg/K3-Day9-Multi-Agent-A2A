# Member Role Report — Day 9: Multi Agent A2A

> Mỗi thành viên trong nhóm tự hoàn thành mẫu này để báo cáo đúng vai trò, phần việc và mức hiểu của mình. Không sao chép nguyên báo cáo chung hoặc báo cáo của thành viên khác. Thay nội dung trong dấu `[ ]` và xóa các dòng hướng dẫn không cần thiết trước khi nộp.

## 1. Thông tin cá nhân

| Thông tin         | Nội dung           |
| ------------------ | ------------------- |
| Họ và tên       | Lương Thanh Trang |
| MSSV               | 2A202601363         |
| Khóa/Lớp         | K3                  |
| Vai trò chính    | Tự triển khai toàn bộ pipeline (không chia module theo người) — bài lab chạy theo kiểu mỗi thành viên tự làm hết 6 agent + verifier + validator trong checkpoint riêng, sau đó cả nhóm so kết quả với nhau (Cuong, Khuat Van Vuong) trước khi chốt bản nộp chung |
| Ngày hoàn thành | 2026-08-05          |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao  | Trạng thái                                 |
| ------------------ | --------------------- | ---------------- | ----------------- | -------------------------------------------- |
| Data layer & bundling | `src/data_store.py`, `src/config.py` | 9 CSV Olist trong `data/` | `OrderBundle` join theo `claimed_order_id` (orders, items, payments, customers) | Hoàn thành |
| Coordinator + LangGraph | `src/agents/coordinator.py`, `src/graph.py`, `src/main.py` | `input/EC_xxx.json`, `OrderBundle` | `bundle_view` phát cho 3 domain agent, ghi `output/EC_xxx.json` | Hoàn thành |
| Order & Seller / Payment / Delivery Agent | `src/agents/order_seller.py`, `payment.py`, `delivery.py` | `bundle_view` (lát cắt tương ứng) | `findings.order_seller/payment/delivery` (fan-out song song) | Hoàn thành |
| Policy Agent | `src/agents/policy.py`, `src/schema.py` | `state.findings` (không đọc lại CSV) | `draft` (primary_issue, refund, evidence, action) | Hoàn thành |
| Verifier Agent + validator độc lập | `src/agents/verifier.py`, `tools/validate_output.py` | `draft` + `bundle_view` | `verification`, báo cáo khớp/lệch so với CSV tính lại bằng pandas | Hoàn thành |
| Trace & metadata | `src/tracing.py`, `logging/trace.jsonl`, `logging/trace_events.jsonl`, `metadata.json` | sự kiện mỗi agent trong 1 lượt `--all` | trace 50/50 case, metadata model/runtime | Hoàn thành |
| Kiến trúc & tài liệu | `architecture.md`, `kien-truc-multi-agent.drawio/.svg` | luồng handoff thực tế trong code | sơ đồ agent, bảng quyền truy cập, bảng trạng thái kiểm chứng | Hoàn thành |

Vì cách tổ chức lab là "ai cũng làm hết một lượt rồi so sánh", bảng trên liệt kê toàn bộ pipeline do tôi tự chạy và tự kiểm chứng, không phải một lát cắt module do người khác giao.

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động                  | Thành viên/module được hỗ trợ | Kết quả                    |
| ----------------------------- | ------------------------------------ | ---------------------------- |
| So sánh kết quả phân loại `primary_issue` trên cùng 50 case | Bài làm của Cuong, Khuat Van Vuong (cùng repo, commit `b26ab92`, `c270ab5`, `5552f79`) | Đối chiếu để phát hiện case lệch quy tắc ưu tiên trước khi chốt bản nộp chung |
| Rà lại yêu cầu README mục 8 (trace/metadata phải nằm ở root repo) | Toàn nhóm | Phát hiện thiếu, bổ sung `mirror_to_repo_root()` — xem mục 6 |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao       | Cách xác minh  |
| --------------------------- | ----------------------------- | ------------------------- | ---------------- |
| Chạy full pipeline 50 case qua LangGraph | `src/main.py`, `logging/trace.jsonl` | 50/50 file `output/EC_001.json`…`EC_050.json`, mỗi case đi đủ 7 node (fan-out 3 domain agent, hội tụ Policy, Verifier) | `py -3 -m src.main --all`, đếm dòng `logging/trace.jsonl` khớp 50 case |
| Đối chiếu độc lập bằng validator không dùng LLM | `tools/validate_output.py` | Validator tự suy `primary_issue`, refund, evidence từ CSV bằng pandas rồi so với `output/`, không import `src/` | `py -3 tools/validate_output.py` — khớp 50/50, 0 evidence sai định dạng/không có thật |
| Vẽ và chốt kiến trúc agent | `architecture.md`, `kien-truc-multi-agent.drawio/.svg` | Sơ đồ handoff, bảng quyền đọc/ghi từng agent, bảng trạng thái đã kiểm chứng theo từng thành phần | Đối chiếu từng dòng bảng mục 5 `architecture.md` với `trace.jsonl` |

Output cụ thể: `logging/trace.jsonl` dòng cuối (case `EC_049`) ghi `agent_path` đủ trình tự `coordinator → delivery → order_seller → payment → policy → verifier → coordinator`, `n_llm_calls: 4`, kết quả `late_delivery_logistics`, `recommended_refund_brl: 15.31` — khớp với `output/EC_049.json` và được `tools/validate_output.py` xác nhận lại từ CSV gốc.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Một khiếu nại "giao trễ" không thể quy trách nhiệm chỉ từ lời khách hàng: phải join order với item, payment, seller và mốc thời gian để biết seller có bàn giao đúng `shipping_limit_date` hay không, và đơn vị vận chuyển có giao trễ so với `order_estimated_delivery_date` hay không — hai nguyên nhân dẫn tới hai bên chịu trách nhiệm khác nhau (`late_delivery_seller` vs `late_delivery_logistics`). Pipeline phải tách được việc này bằng dữ liệu có thể kiểm chứng, không để LLM tự suy diễn sự kiện không có trong CSV (Olist không có refund ledger hay tracking checkpoint theo item).

### Cách triển khai

Rule engine tất định quyết toàn bộ nghiệp vụ, LLM chỉ là lớp xác nhận độc lập song song:

- `Coordinator` join 4 bảng CSV theo `claimed_order_id` thành `OrderBundle`, phát `bundle_view` — mỗi domain agent chỉ nhận đúng lát cắt của mình (Order & Seller không thấy payment, Payment không thấy timestamp giao hàng…), tránh một agent tự suy luận ngoài phạm vi.
- 3 domain agent chạy song song (fan-out LangGraph) rồi hội tụ vào `state.findings` qua reducer `merge_findings`.
- `Policy Agent` áp `ISSUE_PRIORITY` — duyệt đúng 6 quy tắc theo thứ tự ưu tiên trong `EC_POLICY_V1`, dừng ở quy tắc đầu tiên khớp điều kiện (`policy.py:_classify`), không dùng LLM để chọn `primary_issue` — LLM chỉ gọi thêm để tự phân loại độc lập trên cùng finding, dùng làm phiếu vote hiệu chỉnh `confidence` (`BASE_CONFIDENCE ± AGREE_BONUS/DISAGREE_PENALTY`, giới hạn `[0.50, 0.98]`), không ghi đè kết luận.
- Evidence ID chỉ dựng qua helper `ev_order/ev_item/ev_payment/ev_seller/ev_policy` trong `schema.py`, không nối chuỗi thủ công, để không tạo ra ID không tồn tại trong dữ liệu.
- `Verifier Agent` (trong pipeline) và `tools/validate_output.py` (độc lập, không import `src/`, không gọi LLM, tự tính lại bằng pandas) kiểm hai lần: schema, giới hạn số lượng ID, evidence có join được về CSV, và refund khớp `case_status`/`action`.

### Input, output và contract

| Thành phần                   | Mô tả                                     |
| ------------------------------ | ------------------------------------------- |
| Input                          | `input/EC_xxx.json` (`case_id`, `opened_at`, `customer_request.claimed_order_id`, `policy_version`) |
| Output                         | `output/EC_xxx.json` theo schema `CaseOutput` — `assessment`, `affected_entities`, `root_cause_analysis`, `evidence_ids`, `financial_resolution`, `resolution_actions` |
| Module phụ thuộc             | `src/data_store.py` (9 CSV `data/`), `src/schema.py` (bảng `ISSUE_PRIORITY`/`ISSUE_ACTION`/`ISSUE_ROOT_CAUSE`), `src/llm.py` (gọi model qua groq/openrouter) |
| Module sử dụng output        | `src/agents/verifier.py`, `tools/validate_output.py`, `src/main.py` (ghi file JSON cuối cùng) |
| Điều kiện lỗi cần xử lý | Order không có item row (item_ids/seller_ids rỗng, 2 khoản tiền = 0.0); LLM lỗi hoặc bất đồng (`confidence` giảm, kết luận nghiệp vụ giữ nguyên); evidence không dựng được từ CSV của đúng order đó → `evidence_not_in_data` |

### Cách xác minh

```bash
py -3 -m src.main --all
py -3 tools/validate_output.py
```

- **Kết quả mong đợi:** 50/50 file `output/EC_xxx.json` hợp lệ, verifier trong pipeline báo `ok: true`, validator độc lập không báo lệch.
- **Kết quả thực tế:** `logging/trace.jsonl` có 50 dòng, mỗi dòng `agent_path` đủ 7 node và `result` khớp file output tương ứng (đối chiếu mẫu `EC_049` ở mục 3); phân bố 8/8/8/8/9/9 case trên 6 quy tắc theo `architecture.md` mục 5.
- **Artifact/log:** `logging/trace.jsonl`, `logging/trace_events.jsonl`, `metadata.json` (không chứa API key — key nằm trong `.env`, không commit).

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Mỗi agent chỉ được dùng model ≤ 10B tham số (`llama-3.1-8b-instant`), model nhỏ dễ bất nhất, dễ tự bịa sự kiện nếu để nó quyết định trực tiếp số tiền hoàn và evidence ID trên dữ liệu tài chính thật.
- **Các phương án đã cân nhắc:** (1) Để LLM đọc `bundle_view` và tự trả về toàn bộ JSON output (prompt-driven); (2) rule engine tất định quyết `primary_issue`/refund/evidence, LLM chỉ đóng vai second opinion song song để hiệu chỉnh `confidence` và ghi `notes` diễn giải.
- **Phương án đã chọn:** (2) — rule engine quyết, LLM không được ghi đè.
- **Lý do:** Đề yêu cầu ưu tiên dữ liệu kiểm chứng được thay vì tin claim/sinh sự kiện không tồn tại (`README.md` mục 1, 4). Với model 8B, để LLM tự tính tiền hoặc tự bịa evidence ID rủi ro cao hơn nhiều so với việc dùng nó như một "giám sát viên" bỏ phiếu đồng thuận — sai của LLM chỉ làm giảm `confidence`, không làm sai số tiền hay evidence.
- **Bằng chứng quyết định phù hợp:** `tools/validate_output.py` (không dùng LLM, tự tính lại từ CSV) khớp 50/50 case với `output/`, sai số tiền < 0.005 BRL, 0 evidence sai định dạng/không tồn tại — nếu để LLM tự quyết số liệu thì không có đường nào đối chiếu độc lập kiểu này.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** README mục 8 yêu cầu `trace.jsonl` và `metadata.json` phải nằm "trong repo" (ở root), nhưng code ban đầu chỉ ghi hai file này vào `logging/trace.jsonl` và `logging/metadata.json` — không có bản nào ở root.
- **Lệnh hoặc bước tái hiện:** `py -3 -m src.main --all` xong, kiểm `dir` ở root repo — không thấy `trace.jsonl`/`metadata.json` ngang hàng `architecture.md`.
- **Nguyên nhân gốc:** `write_metadata()` trong `src/tracing.py` chỉ ghi vào `LOGGING_DIR`, chưa có bước mirror ra `REPO_ROOT` theo đúng chữ yêu cầu nộp bài.
- **Cách xử lý:** Thêm hàm `mirror_to_repo_root()` trong `src/tracing.py`, gọi ngay sau `write_metadata()` — copy nội dung `TRACE_PATH` và `METADATA_PATH` ra file cùng tên ở `REPO_ROOT`, giữ bản gốc ở `logging/` làm nguồn thật.
- **Cách xác minh sau khi sửa:** Chạy lại `py -3 -m src.main --all`, thấy `trace.jsonl` và `metadata.json` xuất hiện ở root, nội dung khớp byte-for-byte với bản trong `logging/` (commit `5bb742d`).
- **Điều học được:** Đọc kỹ mục nộp bài trước khi coi pipeline là "xong" — logic nghiệp vụ đúng không đồng nghĩa layout file nộp đúng; nên đối chiếu checklist nộp bài (mục 8 README) như một bước kiểm riêng, tách khỏi kiểm logic.

## 7. Hiểu biết về luồng end-to-end

Giải thích ngắn gọn bằng lời của bạn:

1. Dữ liệu đi từ Crossref đến vector index như thế nào?
2. Evaluation set và ground-truth document IDs dùng để đo retrieval/answer quality ra sao?
3. Quality checks khác freshness monitoring ở điểm nào trong bài lab?
4. Vì sao phải dùng cùng test set cho baseline, corrupted và repaired?
5. Repair được xem là thành công dựa trên artifact và metric nào?

**Câu trả lời:**

1. **Dữ liệu đi từ Crossref đến vector index như thế nào?** Pipeline K3 Day 9 không dùng Crossref và không dựng vector index — không có bước crawl API ngoài, không embedding, không similarity search. Nguồn dữ liệu là 9 file CSV Olist tĩnh trong `data/`. `Coordinator` (`src/data_store.py`) lấy `claimed_order_id` từ `input/EC_xxx.json`, join theo khóa quan hệ (`order_id`, `customer_id`, `product_id`, `seller_id`) thành một `OrderBundle`, rồi cắt lát thành `bundle_view` phát cho 3 domain agent. Đường đi dữ liệu là join SQL-style tất định trên CSV, không phải index hóa vector rồi retrieve.
2. **Evaluation set và ground-truth document IDs dùng để đo retrieval/answer quality ra sao?** Không có evaluation set/document IDs kiểu retrieval vì bài lab không truy hồi tài liệu. "Ground truth" tương đương ở đây là `tools/validate_output.py`: đọc thẳng CSV bằng pandas, tự suy `primary_issue`/refund/evidence cho từng case theo đúng bảng quy tắc README mục 4-6 — độc lập hoàn toàn với rule engine trong `src/` — rồi so với `output/EC_xxx.json`. Evidence ID (`order:`, `item:`, `payment:`, `seller:`, `policy:`) đóng vai trò tương đương document ID: phải join được về đúng CSV của order đó, sai định dạng hoặc không tồn tại bị tính lỗi.
3. **Quality checks khác freshness monitoring ở điểm nào trong bài lab?** Bài lab không có freshness monitoring vì dữ liệu là snapshot CSV cố định, không phải nguồn sống cần theo dõi độ mới/độ trễ cập nhật. Quality check ở đây là kiểm tính đúng tại một thời điểm dữ liệu cố định, gồm hai lớp: `Verifier Agent` trong pipeline (schema, giới hạn số ID, evidence join được về CSV) và validator độc lập `tools/validate_output.py` chạy ngoài pipeline, không gọi LLM.
4. **Vì sao phải dùng cùng test set cho baseline, corrupted và repaired?** Bài lab này không có baseline/corrupted/repaired — chỉ một lượt input cố định (50 case `EC_001`-`EC_050`) chạy một lần qua pipeline. Nguyên tắc vẫn giữ nguyên lý do tương tự: rule engine và validator độc lập phải chạy trên đúng cùng 50 case đó thì kết quả mới so sánh được — đổi input hoặc lệch phiên bản CSV sẽ ra số khác, không còn phản ánh đúng lỗi nằm ở logic hay ở dữ liệu.
5. **Repair được xem là thành công dựa trên artifact và metric nào?** Không có bước repair trong bài lab này. Tiêu chí "case xử lý đúng" tương đương gồm ba điều kiện: (a) `logging/trace.jsonl` có dòng `ok: true`, đủ `agent_path` 7 node; (b) `output/EC_xxx.json` khớp schema và giới hạn số lượng ID (README mục 6); (c) `tools/validate_output.py` xác nhận `primary_issue`, refund, evidence khớp đáp án tự tính từ CSV. Cả 3 đã đạt cho 50/50 case theo bảng mục 5 của `architecture.md`.

## 8. Cam kết của thành viên

Đánh dấu sau khi tự kiểm tra:

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi "đã chạy thành công" cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Lương Thanh Trang
**Ngày xác nhận:** 2026-08-05
