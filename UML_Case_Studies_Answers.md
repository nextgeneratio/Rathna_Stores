# Requirements and UML Analysis — Case Studies

---

## Case Study 1 — Online Library

### Functional Requirements

1. Students shall be able to search for books by **title, author, or ISBN**.
2. Students shall be able to **reserve available books**.
3. Students shall be able to **cancel their reservations**.
4. Authorized librarians shall be able to **add, update, and remove book records**.

### Non-Functional Requirements

1. **Performance:** Search results shall be displayed within **3 seconds**.
2. **Security:** Only **authorized librarians** shall be allowed to modify book information.
3. **Reliability:** The system should reliably store and retrieve book and reservation information.

### Class Diagram

Possible classes:

- **Student**
  - studentId
  - name
  - email
- **Librarian**
  - librarianId
  - name
  - email
- **Book**
  - bookId
  - title
  - author
  - ISBN
  - availabilityStatus
- **Reservation**
  - reservationId
  - reservationDate
  - status

Relationships:

- A **Student** can make many **Reservations**.
- A **Reservation** is associated with one **Book**.
- A **Librarian** manages many **Books**.
- A **Student** can have multiple reservations.

### Use Case Diagram

**Actors:**

- Student
- Librarian

**Student goals/use cases:**

- Search Book
- Reserve Book
- Cancel Reservation

**Librarian goals/use cases:**

- Add Book
- Update Book
- Remove Book

### Activity Diagram

A suitable workflow is **Book Reservation**:

1. Student searches for a book.
2. System displays matching books.
3. Student selects a book.
4. System checks availability.
5. If available, system creates the reservation.
6. System confirms the reservation.
7. If unavailable, system informs the student.

### Sequence Diagram

**Operation: Reserve a Book**

Possible sequence:

1. Student → Library System: Search for book.
2. Library System → Book Database: Check book details.
3. Book Database → Library System: Return book information.
4. Library System → Student: Display available book.
5. Student → Library System: Request reservation.
6. Library System → Book Database: Check availability.
7. Book Database → Library System: Confirm availability.
8. Library System → Reservation Database: Create reservation.
9. Reservation Database → Library System: Reservation saved.
10. Library System → Student: Display reservation confirmation.

---

## Case Study 2 — Food Delivery System

### Functional Requirements

1. Customers shall be able to **browse restaurants and view menus**.
2. Customers shall be able to **place food orders**.
3. Customers shall be able to **make online payments**.
4. Restaurant staff shall be able to **accept/reject orders and update order status**.

### Non-Functional Requirements

1. **Availability:** The system shall be available **24/7**.
2. **Security:** Payment information shall be **transmitted securely**.
3. **Reliability:** Orders and payment transactions should be processed reliably without data loss.

### Class Diagram

Possible classes:

- **Customer**
  - customerId
  - name
  - phone
  - address
- **Restaurant**
  - restaurantId
  - name
  - address
- **Menu**
  - menuId
  - name
- **MenuItem**
  - itemId
  - name
  - price
- **Order**
  - orderId
  - orderDate
  - status
  - totalAmount
- **Payment**
  - paymentId
  - amount
  - paymentStatus
- **RestaurantStaff**
  - staffId
  - name

Relationships:

- A **Restaurant** has one or more **MenuItems**.
- A **Customer** can place many **Orders**.
- An **Order** contains one or more **MenuItems**.
- An **Order** has a **Payment**.
- **RestaurantStaff** manages **Orders**.

### Use Case Diagram

**Actors:**

- Customer
- Restaurant Staff
- Payment Service/Bank Gateway

**Customer goals/use cases:**

- Browse Restaurants
- View Menu
- Place Order
- Make Payment

**Restaurant Staff goals/use cases:**

- Accept Order
- Reject Order
- Update Order Status

**Payment Service goals/use case:**

- Process Payment

### Activity Diagram

A suitable workflow is **Placing an Order**:

1. Customer browses restaurants.
2. Customer selects a restaurant.
3. Customer views the menu.
4. Customer selects food items.
5. Customer places the order.
6. System sends order to restaurant.
7. Restaurant accepts or rejects the order.
8. If accepted, customer makes payment.
9. System confirms payment.
10. Restaurant updates order status.
11. Order is delivered/completed.

### Sequence Diagram

**Operation: Place an Order and Make Payment**

1. Customer → Food Delivery System: Select restaurant and food.
2. Customer → Food Delivery System: Submit order.
3. Food Delivery System → Restaurant Staff: Send order.
4. Restaurant Staff → Food Delivery System: Accept order.
5. Food Delivery System → Customer: Request payment.
6. Customer → Payment Gateway: Send payment details.
7. Payment Gateway → Food Delivery System: Payment successful.
8. Food Delivery System → Restaurant Staff: Confirm paid order.
9. Food Delivery System → Customer: Display order confirmation.

---

## Case Study 3 — University Attendance System

### Functional Requirements

1. Lecturers shall be able to **mark students as present or absent**.
2. Lecturers shall be able to **view attendance reports**.
3. Students shall be able to **view their own attendance records**.
4. Only authorized lecturers shall be able to **modify attendance records**.

### Non-Functional Requirements

1. **Performance:** Attendance records shall be displayed within **2 seconds**.
2. **Security:** Only **authorized lecturers** shall be allowed to modify attendance.
3. **Reliability:** Attendance records should be stored accurately and consistently.

### Class Diagram

Possible classes:

- **Student**
  - studentId
  - name
  - email
- **Lecturer**
  - lecturerId
  - name
  - email
- **Course**
  - courseId
  - courseName
- **AttendanceRecord**
  - attendanceId
  - date
  - status

Relationships:

- A **Lecturer** teaches one or more **Courses**.
- A **Student** enrolls in one or more **Courses**.
- A **Course** has many **AttendanceRecords**.
- Each **AttendanceRecord** belongs to a **Student** and a **Course**.
- A **Lecturer** can create/update attendance records.

### Use Case Diagram

**Actors:**

- Lecturer
- Student

**Lecturer goals/use cases:**

- Mark Attendance
- Update Attendance
- View Attendance Reports

**Student goals/use cases:**

- View Own Attendance

### Activity Diagram

A suitable workflow is **Mark Attendance**:

1. Lecturer logs in.
2. Lecturer selects a course/class.
3. System displays enrolled students.
4. Lecturer marks each student as present or absent.
5. Lecturer submits attendance.
6. System validates lecturer authorization.
7. System saves attendance records.
8. System confirms successful submission.

### Sequence Diagram

**Operation: Mark Attendance**

1. Lecturer → Attendance System: Log in.
2. Attendance System → User Database: Verify lecturer.
3. User Database → Attendance System: Authorization successful.
4. Lecturer → Attendance System: Select course.
5. Attendance System → Database: Retrieve student list.
6. Database → Attendance System: Return student list.
7. Lecturer → Attendance System: Submit attendance.
8. Attendance System → Database: Save attendance records.
9. Database → Attendance System: Confirm saved records.
10. Attendance System → Lecturer: Display confirmation.

---

## Case Study 4 — Online Banking

### Functional Requirements

1. Customers shall be able to **log in** to their accounts.
2. Customers shall be able to **check account balances**.
3. Customers shall be able to **transfer money**.
4. Customers shall be able to **view transaction history**.
5. The system shall **lock an account after three incorrect login attempts**.

### Non-Functional Requirements

1. **Security:** All sensitive information shall be **encrypted**.
2. **Performance:** A transaction shall be completed within **5 seconds under normal conditions**.
3. **Reliability:** Transactions should be processed accurately without loss or duplication.

### Class Diagram

Possible classes:

- **Customer**
  - customerId
  - name
  - username
  - password
- **BankAccount**
  - accountNumber
  - accountType
  - balance
  - status
- **Transaction**
  - transactionId
  - date
  - amount
  - type
  - status
- **LoginAttempt**
  - attemptId
  - attemptCount
  - timestamp

Relationships:

- A **Customer** can own one or more **BankAccounts**.
- A **BankAccount** has many **Transactions**.
- A **Transaction** may transfer money between two **BankAccounts**.
- **LoginAttempt** tracks failed authentication attempts for a customer/account.

### Use Case Diagram

**Actors:**

- Customer
- Bank System / Authentication Service

**Customer goals/use cases:**

- Log In
- Check Balance
- Transfer Money
- View Transaction History

**System behavior:**

- Lock Account After 3 Failed Attempts

### Activity Diagram

A suitable workflow is **Money Transfer**:

1. Customer logs in.
2. System verifies credentials.
3. Customer selects transfer option.
4. Customer enters recipient and amount.
5. System validates the account and amount.
6. System checks sufficient balance.
7. System processes the transfer.
8. System records the transaction.
9. System displays confirmation.

### Sequence Diagram

**Operation: Transfer Money**

1. Customer → Banking System: Log in.
2. Banking System → Authentication Service: Verify credentials.
3. Authentication Service → Banking System: Authentication successful.
4. Customer → Banking System: Enter transfer details.
5. Banking System → Account Database: Check balance and recipient.
6. Account Database → Banking System: Validation successful.
7. Banking System → Account Database: Debit sender account.
8. Banking System → Account Database: Credit recipient account.
9. Banking System → Transaction Database: Record transaction.
10. Banking System → Customer: Display transfer confirmation.

---

## Case Study 5 — Hospital Appointment System

### Functional Requirements

1. Patients shall be able to **register** in the system.
2. Patients shall be able to **search for doctors**.
3. Patients shall be able to **view available appointment slots**.
4. Patients shall be able to **book and cancel appointments**.
5. Doctors shall be able to **view their appointments**.

### Non-Functional Requirements

1. **Security/Privacy:** Patient information shall be kept **confidential**.
2. **Availability:** The system shall be available with **minimal downtime**.
3. **Reliability:** Appointment bookings and cancellations should be recorded accurately.

### Class Diagram

Possible classes:

- **Patient**
  - patientId
  - name
  - dateOfBirth
  - contactNumber
- **Doctor**
  - doctorId
  - name
  - specialization
- **Appointment**
  - appointmentId
  - date
  - time
  - status

Relationships:

- A **Patient** can have many **Appointments**.
- A **Doctor** can have many **Appointments**.
- An **Appointment** belongs to one **Patient** and one **Doctor**.
- A **Doctor** can provide multiple available appointment slots.

### Use Case Diagram

**Actors:**

- Patient
- Doctor

**Patient goals/use cases:**

- Register
- Search Doctor
- View Available Slots
- Book Appointment
- Cancel Appointment

**Doctor goals/use case:**

- View Appointments

### Activity Diagram

A suitable workflow is **Book Appointment**:

1. Patient logs in/registers.
2. Patient searches for a doctor.
3. System displays doctors.
4. Patient selects a doctor.
5. System displays available slots.
6. Patient selects a slot.
7. System checks slot availability.
8. System creates the appointment.
9. System confirms the booking.

### Sequence Diagram

**Operation: Book an Appointment**

1. Patient → Appointment System: Search doctor.
2. Appointment System → Doctor Database: Find doctor.
3. Doctor Database → Appointment System: Return doctor details.
4. Patient → Appointment System: Request available slots.
5. Appointment System → Appointment Database: Retrieve available slots.
6. Appointment Database → Appointment System: Return slots.
7. Patient → Appointment System: Select slot and book.
8. Appointment System → Appointment Database: Save appointment.
9. Appointment Database → Appointment System: Confirm booking.
10. Appointment System → Patient: Display appointment confirmation.

---

## Case Study 6 — Smart Parking System

### Functional Requirements

1. The system shall **display available parking spaces** to drivers.
2. The system shall **detect vehicle entry**.
3. The system shall **assign an available parking space** to a vehicle.
4. When a vehicle leaves, the system shall **calculate the parking fee**.

### Non-Functional Requirements

1. **Performance:** Vehicle entry shall be detected within **2 seconds**.
2. **Reliability/Availability:** The system shall operate reliably **throughout the day**.
3. **Accuracy:** Parking space availability and parking fees should be calculated accurately.

### Class Diagram

Possible classes:

- **Driver**
  - driverId
  - name
- **Vehicle**
  - vehicleId
  - plateNumber
- **ParkingSpace**
  - spaceId
  - location
  - status
- **ParkingSession**
  - sessionId
  - entryTime
  - exitTime
  - fee

Relationships:

- A **Driver** may own one or more **Vehicles**.
- A **Vehicle** has a **ParkingSession**.
- A **ParkingSession** is assigned to one **ParkingSpace**.
- A **ParkingSpace** can have many parking sessions over time, but only one active session at a time.

### Use Case Diagram

**Actors:**

- Driver
- Entry Sensor
- Exit Sensor

**Driver goals/use cases:**

- View Available Spaces
- Park Vehicle
- Leave Parking Area
- Pay Parking Fee

**Sensor/system interactions:**

- Detect Vehicle Entry
- Detect Vehicle Exit

### Activity Diagram

A suitable workflow is **Vehicle Entry and Exit**:

1. System detects vehicle entry.
2. System checks available spaces.
3. If a space is available, assign a space.
4. Record entry time.
5. Driver parks vehicle.
6. Driver later exits.
7. System detects vehicle exit.
8. System calculates parking duration.
9. System calculates parking fee.
10. System displays the fee and closes the parking session.

### Sequence Diagram

**Operation: Assign Parking Space**

1. Entry Sensor → Parking System: Detect vehicle entry.
2. Parking System → Parking Database: Request available spaces.
3. Parking Database → Parking System: Return available space.
4. Parking System → Parking Database: Assign space to vehicle.
5. Parking Database → Parking System: Confirm assignment.
6. Parking System → Driver: Display assigned parking space.

---

## Case Study 7 — Online Examination System

### Functional Requirements

1. Students shall be able to **log in** to the examination system.
2. Students shall be able to **view available examinations**.
3. Students shall be able to **answer and submit examination questions**.
4. The system shall **automatically save answers while an examination is in progress**.
5. Lecturers shall be able to **create examinations and view student results**.

### Non-Functional Requirements

1. **Performance/Scalability:** The system shall support **500 students simultaneously** without significant performance degradation.
2. **Reliability:** Student answers should be saved reliably during the examination.
3. **Availability:** The system should remain available throughout scheduled examinations.

### Class Diagram

Possible classes:

- **Student**
  - studentId
  - name
  - email
- **Lecturer**
  - lecturerId
  - name
  - email
- **Examination**
  - examId
  - title
  - startTime
  - endTime
- **Question**
  - questionId
  - questionText
  - marks
- **Answer**
  - answerId
  - answerText
  - savedTime
- **Result**
  - resultId
  - score
  - grade

Relationships:

- A **Lecturer** can create many **Examinations**.
- An **Examination** contains many **Questions**.
- A **Student** can take many **Examinations**.
- A **Student** provides many **Answers**.
- An **Answer** belongs to one **Question** and one **Student**.
- An **Examination** produces **Results** for students.

### Use Case Diagram

**Actors:**

- Student
- Lecturer

**Student goals/use cases:**

- Log In
- View Available Examinations
- Answer Questions
- Submit Answers

**Lecturer goals/use cases:**

- Create Examination
- View Student Results

**System behavior:**

- Auto-save Answers During Examination

### Activity Diagram

A suitable workflow is **Taking an Examination**:

1. Student logs in.
2. System authenticates the student.
3. Student views available examinations.
4. Student selects an examination.
5. System displays questions.
6. Student answers questions.
7. System automatically saves answers.
8. Student continues until finished.
9. Student submits the examination.
10. System stores the final answers.
11. System confirms submission.

### Sequence Diagram

**Operation: Submit Examination**

1. Student → Examination System: Log in.
2. Examination System → User Database: Verify student.
3. User Database → Examination System: Authentication successful.
4. Student → Examination System: Select examination.
5. Examination System → Exam Database: Retrieve questions.
6. Exam Database → Examination System: Return questions.
7. Student → Examination System: Submit answers.
8. Examination System → Answer Database: Save answers.
9. Answer Database → Examination System: Confirm saved answers.
10. Examination System → Result Service: Evaluate answers.
11. Result Service → Examination System: Return result.
12. Examination System → Student: Display submission/result confirmation.

---

# Quick Summary of Diagram Purposes

| Diagram | What it represents |
|---|---|
| **Class Diagram** | Classes, attributes, methods, and relationships between objects/classes |
| **Use Case Diagram** | Actors and the functions/goals they interact with |
| **Activity Diagram** | Step-by-step workflow or business process |
| **Sequence Diagram** | Order of interactions/messages between actors and system objects over time |

