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

[Phần của bạn giải quyết vấn đề gì trong pipeline?]

### Cách triển khai

[Mô tả thuật toán, quy tắc dữ liệu, orchestration hoặc quyết định chính. Không chỉ chép lại tên hàm.]

### Input, output và contract

| Thành phần                   | Mô tả                                     |
| ------------------------------ | ------------------------------------------- |
| Input                          | [Schema, artifact hoặc tham số]           |
| Output                         | [Schema, artifact hoặc giá trị trả về] |
| Module phụ thuộc             | [Module/file liên quan]                    |
| Module sử dụng output        | [Module/file liên quan]                    |
| Điều kiện lỗi cần xử lý | [Trường hợp thực tế]                   |

### Cách xác minh

```bash
[Ghi lệnh thực tế đã chạy]
```

- **Kết quả mong đợi:** [Mô tả.]
- **Kết quả thực tế:** [Mô tả.]
- **Artifact/log:** [Đường dẫn; không chứa secret.]

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** [Vấn đề hoặc lựa chọn cần quyết định.]
- **Các phương án đã cân nhắc:** [Ít nhất hai phương án.]
- **Phương án đã chọn:** [Lựa chọn.]
- **Lý do:** [Trade-off về correctness, data quality, reproducibility, cost hoặc độ phức tạp.]
- **Bằng chứng quyết định phù hợp:** [Metric, artifact hoặc kết quả thử nghiệm.]

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** [Che toàn bộ secret trước khi ghi.]
- **Lệnh hoặc bước tái hiện:** [Lệnh/bước.]
- **Nguyên nhân gốc:** [Root cause, không chỉ mô tả triệu chứng.]
- **Cách xử lý:** [Thay đổi cụ thể.]
- **Cách xác minh sau khi sửa:** [Lệnh và kết quả.]
- **Điều học được:** [Bài học kỹ thuật.]

Nếu chưa xử lý xong:

- **Phạm vi bị ảnh hưởng:** [Module/artifact.]
- **Những gì đã loại trừ:** [Các giả thuyết đã kiểm tra.]
- **Bước tiếp theo:** [Hành động có thể kiểm chứng.]

## 7. Hiểu biết về luồng end-to-end

Giải thích ngắn gọn bằng lời của bạn:

1. Dữ liệu đi từ Crossref đến vector index như thế nào?
2. Evaluation set và ground-truth document IDs dùng để đo retrieval/answer quality ra sao?
3. Quality checks khác freshness monitoring ở điểm nào trong bài lab?
4. Vì sao phải dùng cùng test set cho baseline, corrupted và repaired?
5. Repair được xem là thành công dựa trên artifact và metric nào?

**Câu trả lời:**

[Viết câu trả lời tại đây.]

## 8. Cam kết của thành viên

Đánh dấu sau khi tự kiểm tra:

- [ ] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [ ] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [ ] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [ ] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [ ] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Lương Thanh Trang
**Ngày xác nhận:** 2026-08-05
