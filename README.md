#  Coding Flow Notes

###  Trình độ: Beginner

---

##  Operating System (OS)

- **Hệ điều hành**: Quản lý phần cứng và phần mềm (RAM, CPU, tiến trình, file, thiết bị).
- Cung cấp giao diện người dùng và thực thi các ứng dụng.

---

##  Kernel (Lõi)

- Nhân của OS, viết bằng mã low-level để điều khiển phần cứng.
- Quản lý tiến trình, bộ nhớ, driver, và bảo mật.

---

##  Chipset

- Tập hợp các chip giúp CPU giao tiếp với phần còn lại của bo mạch chủ.
- Điều phối RAM, USB, PCIe, CPU, GPU, thiết bị ngoại vi.

---

##  CPU (Central Processing Unit)

- Xử lý các tác vụ chung, ít lõi hơn GPU nhưng lõi mạnh hơn.
- Dành cho các ứng dụng như web, Word, hệ điều hành.

---

##  GPU (Graphics Processing Unit)

- Xử lý song song tốt hơn, nhiều lõi hơn CPU nhưng yếu hơn.
- Dành cho render game, huấn luyện mô hình AI.

####  Tại sao GPU dùng cho huấn luyện AI?

- Huấn luyện AI cần tính toán ma trận, vector song song.
- GPU có nhiều lõi và được tối ưu để thực hiện cùng lúc nhiều phép tính.
- Băng thông bộ nhớ (bandwidth) cao hơn CPU.

---

##  Kiến trúc CPU

###  AMD

- Cấu trúc: CISC
- Tiêu thụ điện nhiều
- Dành cho máy gaming, hiệu suất đơn luồng cao

###  ARM

- Cấu trúc: RISC
- Tiêu thụ ít điện
- Dành cho laptop, tablet
- Hiệu suất đơn luồng thấp

---

## Data Structures & Algorithms

---

### Time Complexity (Big-O)

- Đo lường mối quan hệ giữa input và số bước xử lý (trường hợp tệ nhất).

#### Ví dụ: Binary Search
- Time complexity: `O(log n)`
- Mỗi lần chia đôi mảng → `n / 2^k = 1` → `k = log₂(n)`

 **Lưu ý**: Binary Search chỉ dùng được khi mảng đã sắp xếp.

---

##  Algorithms (Thuật toán)

###  Sorting Algorithms (Sắp xếp)

#### 1. Merge Sort
- Chia mảng → sắp xếp → ghép lại
- Chia `log n` lần, mỗi lần ghép `O(n)`
- **Time complexity**: `O(n log n)`

#### 2. Selection Sort
- Dùng hai con trỏ: tìm phần tử nhỏ nhất → đổi chỗ
- Quét toàn bộ mảng `n` lần, mỗi lần `O(n)`
- **Time complexity**: `O(n²)`

---

##  Data Structures

###  Array (Mảng)

- Lưu trữ liên tiếp trong bộ nhớ
- Truy cập nhanh bằng chỉ số
- Kích thước cố định, không thêm trực tiếp được

###  Linked List

- Gồm các node có con trỏ tới node kế tiếp
- Thêm/xóa dễ dàng
- Không thể truy cập ngẫu nhiên như array

###  Binary Tree

- Mỗi node có tối đa 2 node con
- Node trái < node mẹ, node phải ≥ node mẹ
- **Search/Insert/Delete**: `O(H)` (trung bình là `O(log n)`)

###  Heap (Priority Queue)

- Triển khai bằng mảng dạng cây
- Có `min-heap` và `max-heap`
- **Insert/Delete**: `O(log n)`, **get top**: `O(1)`

---

##  Tree Traversals

###  DFS (Depth First Search)

- Duyệt theo chiều sâu
- Dùng **stack**

###  BFS (Breadth First Search)

- Duyệt theo từng lớp (level)
- Dùng **queue**

---

## Stack Overflow Là Gì?

- Khi chương trình sử dụng quá nhiều bộ nhớ stack
- Thường xảy ra do **đệ quy quá sâu**

---

##  Graph (Đồ thị)

- Gồm các **đỉnh (node)** và **cạnh (edge)**

###  Loại đồ thị:

- **Directed graph**: Cạnh có chiều
- **Undirected graph**: Cạnh hai chiều
- **Unweighted**: Không có trọng số
- **Weighted**: Có trọng số

---

## Dijkstra’s Algorithm

###  Ý tưởng:

1. Khoảng cách từ đỉnh bắt đầu = 0, các đỉnh khác = ∞
2. Dùng **min-heap** để chọn đỉnh có khoảng cách ngắn nhất
3. Với mỗi hàng xóm, nếu tìm được đường đi ngắn hơn → cập nhật

###  Time Complexity:

- Thiết lập khoảng cách ban đầu: `O(V)`
- Mỗi đỉnh vào heap 1 lần: `O(V log V)`
- Mỗi cạnh có thể cập nhật: `O(E log V)`
- **Tổng cộng**: `O((V + E) log V)`

---

