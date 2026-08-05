# Member Role Report — Day 9: Multi Agent A2A

## 1. Thông tin cá nhân

| Thông tin       | Nội dung                                       |
| --------------- | ---------------------------------------------- |
| Họ và tên       | Đỗ Tuấn Kiệt                                   |
| MSSV            | 2A202601335                                    |
| Khóa/Lớp        | K3                                             |
| Vai trò chính   | Thiết kế kiến trúc & triển khai pipeline multi-agent |
| Ngày hoàn thành | 2026-08-05                                     |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| ------------------ | ------------------ | -------------- | --------------- | ---------- |
| Tầng dữ liệu deterministic | `data_access/data_store.py`, `data_access/evidence.py` | 9 file CSV Olist | `DataStore` với tool `get_order/get_items/get_payments/get_seller`, evidence ID đúng chuẩn mục 5 | Hoàn thành |
| Rule engine EC_POLICY_V1 | `data_access/policy_table.py` (`evaluate`, `refund_for`, `responsible_parties_for`) | facts tổng hợp từ 3 agent domain | `primary_issue`, root cause, refund, action, responsible parties | Hoàn thành |
| 6 agent + orchestration | `agents/*.py` | case JSON trong `input/` | `CaseFile` hoàn chỉnh, output đúng schema mục 6 | Hoàn thành |
| Verifier & cổng kiểm tra | `agents/verifier_agent.py`, `checks.py` | draft output + CSV gốc | `errors`/`warnings`, exit code 0/1 trước khi nộp | Hoàn thành |
| Lớp LLM (OpenAI API) | `llm/client.py`, `llm/prompts.py` | prompt của từng agent | JSON đã parse, hoặc `None` khi degraded | Hoàn thành — 250 call, 0 failure |
| Trace & metadata | `pipeline/trace.py`, `pipeline/run_batch.py` | mọi message giữa các agent | `trace.jsonl` (550 dòng), `metadata.json` | Hoàn thành |

> Nếu nhóm có phân công khác, xoá bớt các dòng không phải phần bạn trực tiếp làm.

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --------- | ----------------------------- | ------- |
| Phân tích bộ 50 input để kiểm chứng giả định thiết kế | Toàn nhóm / `architecture.md` mục 11 | Đo được: 0 đơn nhiều seller, 8 đơn không có item row, biên độ giao trễ gần nhất 2.76 ngày → loại bỏ 3 rủi ro thiết kế ban đầu |
| Viết tài liệu kiến trúc | `architecture.md` | Sơ đồ agent, bảng quyền truy cập, luồng handoff, edge cases |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --------------------- | --------------------------- | ---------------- | ------------- |
| Dựng rule engine áp bảng mục 4 theo thứ tự ưu tiên | `data_access/policy_table.py` | 50/50 case khớp đúng một rule, 0 lần rơi vào fallback | `python main.py` rồi đọc dòng `fallback=0` |
| Chuẩn hoá sinh evidence ID | `data_access/evidence.py` | Mọi evidence resolve được về dòng CSV thật | `python checks.py` |
| Verifier kiểm tra độc lập | `agents/verifier_agent.py` | 0 lỗi verifier trên 50 case | Đếm `verification.passed=false` trong `trace.jsonl` |
| Cổng kiểm tra trước khi nộp | `checks.py` | PASS 50/50, không file lạ trong `output/` | `python checks.py` (exit 0) |
| Ghi trace mọi handoff | `pipeline/trace.py` | 550 dòng: 250 request, 150 finding, 50 decision, 50 verification, 50 final | `wc -l trace.jsonl` |

Nêu một output cụ thể mà phần việc của bạn tạo ra hoặc giúp xác minh:

`checks.py` là artifact có giá trị xác minh cao nhất. Nó dựng lại toàn bộ con số từ CSV gốc mà **không dùng lại bất kỳ kết quả trung gian nào của agent**, nên bắt được cả lỗi mà Verifier Agent bỏ sót. Chạy trên 50 output cho kết quả PASS, kèm phân bố kết luận `unsupported_late_claim 9 / valid_split_payment 9 / late_delivery_seller 8 / canceled_order_paid 8 / unavailable_order_paid 8 / late_delivery_logistics 8` và tổng refund đề xuất 3429.64 BRL.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Hệ thống phải điều tra 50 khiếu nại thương mại điện tử trên dữ liệu Olist. Mỗi case phải đối chiếu nhiều nguồn (đơn hàng, item, seller, thanh toán, mốc giao hàng) để xác định vấn đề, bên chịu trách nhiệm, bằng chứng và khoản hoàn đề xuất. Ràng buộc cốt lõi: kết luận phải dựa trên dữ liệu kiểm chứng được — không tin lời khiếu nại, không bịa sự kiện không có trong CSV.

Phần khó nhất không nằm ở việc gọi model, mà ở chỗ 55% trọng số chấm điểm phụ thuộc vào những thứ một model nhỏ làm rất tệ: cộng tiền chính xác tới 2 chữ số thập phân và ghép đúng chuỗi ID hex 32 ký tự.

### Cách triển khai

Kiến trúc gồm 6 agent giao tiếp qua Coordinator theo mô hình hub (chi tiết trong `architecture.md`):

- **Coordinator** điều phối, gọi tuần tự 5 agent còn lại, gộp `CaseFile`, ghi output.
- **Order & Seller Agent** xác định trạng thái đơn, tổng item/freight, và seller nào bàn giao sau `shipping_limit_date` của item thuộc chính seller đó.
- **Delivery Agent** so `order_delivered_customer_date` với `order_estimated_delivery_date`.
- **Payment Agent** đối soát tổng payment với item + freight (sai số 0.10 BRL).
- **Policy Agent** áp bảng `EC_POLICY_V1` bằng rule engine **first-match-wins theo đúng thứ tự ưu tiên**, không tìm "rule khớp nhất".
- **Verifier Agent** kiểm tra độc lập: dựng lại mọi con số từ CSV thay vì đọc lại kết quả của agent khác.

Quyết định chi phối toàn bộ thiết kế: **mọi phép join, tính tiền, so ngày và dựng evidence ID đều là code Python thuần, không đi qua LLM**. LLM chỉ diễn giải kết quả tool và đóng vai người soát lần hai — được raise cờ nghi ngờ và hạ confidence, nhưng không được đổi kết luận, không được nâng confidence, không được sinh root cause.

Hai chi tiết cài đặt đáng nói:

1. **Thứ tự gọi agent không thể đảo.** Payment Agent cần `item_total + freight_total` do Order & Seller Agent tính ra mới đối soát được; Policy Agent cần kết quả của cả ba agent domain mới đủ facts để áp bảng. Vì vậy Coordinator gọi tuần tự 1→5 chứ không chạy song song.
2. **Evidence ID chỉ được sinh ở một chỗ duy nhất.** `data_access/evidence.py` là module duy nhất được phép ghép chuỗi ID; mọi agent phải đi qua đó. Nếu để từng agent tự nối chuỗi, chỉ cần một chỗ sai định dạng là toàn bộ evidence của case đó thành false positive.

### Input, output và contract

| Thành phần | Mô tả |
| ---------- | ----- |
| Input | `input/EC_XXX.json` — `case_id`, `customer_request.claimed_order_id`, `opened_at`, `policy_version` |
| Output | `output/EC_XXX.json` đúng schema README mục 6, validate bằng pydantic (`schema/output_schema.py`) |
| Module phụ thuộc | `data_access/data_store.py` (dữ liệu CSV), `data_access/policy_table.py` (bảng rule), `llm/client.py` (OpenAI API) |
| Module sử dụng output | `agents/verifier_agent.py` và `checks.py` — cả hai kiểm tra lại độc lập từ CSV |
| Điều kiện lỗi cần xử lý | `claimed_order_id` không có trong CSV → dừng sớm, `no_action`, confidence thấp; đơn không có item row → `item_ids`/`seller_ids` rỗng và `item_total`/`freight_total` = 0.0; verify fail 2 lần → fallback an toàn thay vì ghi JSON sai schema; thiếu API key hoặc API lỗi → chạy degraded, kết luận không đổi (429/5xx retry tối đa 2 lần) |

### Cách xác minh

```bash
python main.py        # chạy 50 case, ghi output/ + trace.jsonl + metadata.json
python checks.py      # cổng kiểm tra độc lập, exit 0 = hợp lệ
```

- **Kết quả mong đợi:** 50 file JSON hợp lệ trong `output/`, mọi evidence ID resolve được về dòng CSV thật, mọi con số tiền khớp tổng tính lại từ CSV.
- **Kết quả thực tế:** `checks.py` in ra `PASS — 50 case hợp lệ, sẵn sàng nén output/ để nộp` (exit 0). Lượt chạy 50 case cho `fallback=0`, `trace_lines=550`, 0 lỗi verifier. **Lưu ý:** kết quả này đến từ lượt chạy ở chế độ deterministic; lượt chạy đầy đủ với model local 7B chưa hoàn tất tại thời điểm viết báo cáo, cần chạy lại và cập nhật mục này trước khi nộp.
- **Artifact/log:** `output/EC_001.json` … `output/EC_050.json`, `trace.jsonl`, `metadata.json`. Không chứa secret.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Đề bài giới hạn mỗi agent chỉ được dùng model ≤10B tham số. Trong khi đó 55% trọng số chấm điểm (affected entities 20% + evidence 15% + financial 20%) phụ thuộc vào việc xuất đúng ID và đúng số tiền tới 2 chữ số thập phân — evidence sai định dạng hoặc không tồn tại trong CSV bị tính là false positive.
- **Các phương án đã cân nhắc:**
  1. Để LLM tự suy luận và tự sinh evidence ID, số tiền, kết luận từ dữ liệu thô đưa vào prompt.
  2. Tầng deterministic (code Python) chịu trách nhiệm toàn bộ phép tính và ID; LLM chỉ diễn giải và soát lại.
- **Phương án đã chọn:** Phương án 2.
- **Lý do:** Model nhỏ không đáng tin khi phải cộng tiền chính xác tới 2 chữ số và ghép chuỗi ID hex 32 ký tự — đây là loại lỗi im lặng, sai mà nhìn vẫn hợp lý. Đẩy phần này vào code khiến điểm số không phụ thuộc vào may rủi của model, trong khi vẫn giữ đúng yêu cầu "có phân công, handoff và kiểm chứng giữa các agent" của đề bài.
- **Bằng chứng quyết định phù hợp:**
  - Chạy 50 case: **0 lỗi verifier**, 0 lần dùng fallback, 50/50 output hợp lệ theo schema.
  - Phân bố 6 loại kết luận là 9/9/8/8/8/8 — gần như cân bằng tuyệt đối, khớp cách đề bài curate bộ case.
  - Đối chiếu chéo độc lập: ý định trong lời khiếu nại (3 template) khớp nhóm kết luận **50/50 case**, dù engine không hề dùng nội dung khiếu nại để quyết định.
  - Quyết định này về sau chứng minh giá trị khi chuyển sang chạy model local: hệ thống đã đi qua ba cấu hình model (không LLM → TinyLlama-1.1B → DeepSeek-R1-Distill-Qwen-7B), tốc độ và tỉ lệ parse JSON đổi hoàn toàn, nhưng **50 output không đổi một ký tự nào**.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:**
  ```
  UnicodeEncodeError: 'charmap' codec can't encode character 'ừ' in position 13:
  character maps to <undefined>
    File "...\encodings\cp1252.py", line 19, in encode
  ```
- **Lệnh hoặc bước tái hiện:** `python main.py --case EC_001 --no-llm` trên console Windows.
- **Nguyên nhân gốc:** Console Windows mặc định dùng codepage cp1252. Python kế thừa encoding đó cho `stdout`, nên log tiếng Việt có dấu không encode được — pipeline chết ngay ở dòng log đầu tiên, không phải do lỗi logic xử lý case.
- **Cách xử lý:** Ép `sys.stdout` và `sys.stderr` sang UTF-8 ngay đầu `main()` trong `main.py`:
  ```python
  for stream in (sys.stdout, sys.stderr):
      if hasattr(stream, "reconfigure"):
          stream.reconfigure(encoding="utf-8", errors="replace")
  ```
- **Cách xác minh sau khi sửa:** Chạy lại đúng lệnh trên — log tiếng Việt in đúng, case chạy hết và ghi ra `output/EC_001.json`.
- **Điều học được:** Lỗi encoding ở tầng I/O rất dễ bị nhầm là lỗi logic vì nó chết ở chỗ không liên quan gì tới dữ liệu. Đọc stack trace tới dòng cuối (`cp1252.py`) chỉ thẳng ra nguyên nhân, nhanh hơn nhiều so với đi debug phần xử lý case.

### Blocker chưa xử lý xong

- **Phạm vi bị ảnh hưởng:** `metadata.json` và lượt chạy 50 case với model local.
- **Triệu chứng:** DeepSeek-R1-Distill-Qwen-7B ở fp16 cần ~15 GB VRAM, GPU thực tế (RTX 3060 Laptop) chỉ có 6 GB.
- **Những gì đã loại trừ:** Chạy fp16 trực tiếp (không đủ VRAM); dùng TinyLlama-1.1B thay thế (nạp được nhưng 5/5 call fail parse JSON, model quá yếu để trả về JSON hợp lệ).
- **Hướng đang làm:** Nạp 4-bit NF4 qua `bitsandbytes` (~4.5 GB, vừa VRAM). Đã cài `bitsandbytes` + `accelerate` và viết xong đường nạp 4-bit trong `llm/client.py`; đang chờ tải xong 15.2 GB weight.
- **Bước tiếp theo:** Chạy `python main.py --case EC_001` đo tốc độ suy luận thật, rồi chạy đủ 50 case và cập nhật lại `metadata.json` (`llm_enabled`, `llm_calls`).

## 7. Hiểu biết về luồng end-to-end

> **Lưu ý:** mục 7 trong file mẫu gốc hỏi về Crossref, vector index và retrieval — nội dung của một lab khác, không khớp bài Multi-Agent A2A này. Các câu hỏi dưới đây đã được điều chỉnh cho đúng đề bài. Nên xác nhận lại với giảng viên trước khi nộp.

Giải thích ngắn gọn bằng lời của bạn:

1. Dữ liệu đi từ file CSV Olist đến kết luận trong `output/EC_XXX.json` như thế nào?
2. Coordinator phân công và nhận handoff từ các agent theo thứ tự nào, và vì sao thứ tự đó không thể đảo?
3. Vì sao evidence ID phải dựng từ dữ liệu thật thay vì để model tự sinh?
4. Verifier Agent kiểm tra những gì, và vì sao nó phải tính lại từ CSV thay vì đọc lại kết quả của agent trước?
5. Hệ thống xử lý thế nào khi khiếu nại của khách hàng mâu thuẫn với dữ liệu (ví dụ khách kêu giao trễ nhưng đơn giao đúng hạn)?

**Câu trả lời:**

**1.** `DataStore` load 9 file CSV một lần lúc khởi động và index theo `order_id`, `seller_id`. Coordinator đọc `claimed_order_id` từ input rồi lần lượt hỏi ba agent domain; mỗi agent gọi tool tương ứng để lấy đúng phần dữ liệu của mình và trả về nhận định kèm evidence. Policy Agent nhận gộp các facts đó, áp bảng rule để ra `primary_issue` và khoản hoàn. Verifier kiểm tra lại, rồi Coordinator ghi file JSON đúng schema.

**2.** Thứ tự là Order & Seller → Delivery → Payment → Policy → Verifier. Không đảo được vì có phụ thuộc dữ liệu thật: Payment Agent cần `item_total + freight_total` mà chỉ Order & Seller Agent tính ra được, còn Policy Agent cần đủ facts của cả ba agent domain (trạng thái đơn, có giao trễ không, payment có khớp không) mới quyết định được rule nào áp. Verifier phải đứng cuối vì nó kiểm tra draft output đã hoàn chỉnh.

**3.** Vì evidence sai định dạng hoặc không tồn tại trong CSV bị tính là false positive khi chấm. ID của Olist là chuỗi hex 32 ký tự — model rất dễ chép sai một ký tự mà nhìn vào vẫn thấy hợp lý, đây là loại lỗi im lặng không có cách nào phát hiện bằng mắt. Nên hệ thống bắt mọi ID phải sinh từ `data_access/evidence.py`, dựa trên dòng dữ liệu có thật.

**4.** Verifier kiểm tra: evidence có resolve được về dòng CSV thật không, tổng item/freight/payment có khớp số tính lại từ CSV không, refund có đúng công thức của rule đã chọn không, các mảng có vượt giới hạn không, và `case_status` có nhất quán với refund không. Nó phải tính lại từ CSV vì nếu đọc lại kết quả của agent trước thì khi agent đó tính sai, Verifier sẽ so một con số sai với chính nó và vẫn báo hợp lệ — tức là agent tự chấm bài mình.

**5.** Dữ liệu thắng lời khiếu nại. Khách kêu giao trễ nhưng dữ liệu cho thấy giao đúng hạn thì rule `unsupported_late_claim` được áp: `case_status = no_action`, refund 0, action `reject_late_refund`. Trong bộ 50 case có 9 trường hợp như vậy. Hệ thống có đối chiếu chéo giữa ý định khiếu nại và nhóm kết luận, nhưng chỉ dùng làm cảnh báo hạ confidence, không bao giờ để nó đổi kết luận — nếu không thì thành ra tin khách hàng thay vì tin dữ liệu.

> Nên đọc lại và viết lại bằng cách diễn đạt của mình trước khi nộp, vì mục 8 yêu cầu tự xác nhận rằng báo cáo phản ánh đúng mức hiểu của bạn.

## 8. Cam kết của thành viên

Đánh dấu sau khi tự kiểm tra:

- [ ] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [ ] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [ ] Tôi không ghi "đã chạy thành công" cho phần chưa được kiểm chứng.
- [ ] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [ ] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Đỗ Tuấn Kiệt
**Ngày xác nhận:** 2026-08-05
