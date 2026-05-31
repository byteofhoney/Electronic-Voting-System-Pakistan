## 🗳️ Electronic Voting System — Pakistan

A Full Stack web application simulating a secure, role based electronic voting system for Pakistan's general elections. Built with Flask and MySQL, the system models the real *ECP (Election Commission of Pakistan)* administrative hierarchy and supports Pakistan's dual constituency structure i.e. National Assembly (NA) and Provincial Assembly (PA) seats.

---

### Overview

The system supports **four distinct user roles**, each with isolated dashboards and permissions:

| Role | Access | Key Responsibility |
|---|---|---|
| **ECP Admin** | Full system | Manage elections, parties, provincial officers, view results |
| **Provincial Officer** | One province | Manage voters, candidates, polling stations, polling officers |
| **Polling Officer** | One station | Verify voters in real time at the polling machine |
| **Voter** | Ballot only | Authenticate by CNIC, cast NA + PA votes |

---

### Screenshots

**Voter Terminal**
![Voter Terminal](screenshots/voter%20terminal.png)

**Staff Login**
![Staff Login](screenshots/staff%20login.png)

**ECP Admin Dashboard**
![ECP Dashboard](screenshots/ecp%20dashboard.png)

---

### Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask, Jinja2 |
| Database | MySQL (MySQL Workbench) |
| Frontend | HTML5, CSS3, Vanilla JavaScript |
| Auth | SHA-256 password hashing, Flask sessions |
| File Uploads | Werkzeug `secure_filename` |
| Environment | `python-dotenv` (.env file) |

---

### Architecture

Three tier web architecture:

```
Presentation Tier    →    templates/ + static/
                              Jinja2-rendered HTML, one folder per role
                              Custom CSS design system (Pakistani national theme)
                              Bilingual UI (English + Urdu, Noto Nastaliq font)

Application Tier     →    blueprints/
                              auth       : shared login/logout
                              ecp        : election & system management
                              provincial : province level administration
                              polling_officer : real time voter verification
                              voter      : CNIC auth + ballot + vote casting

Data Tier            →    database/ + MySQL
                              Normalized schema with foreign key constraints
                              Province/constituency seed data for Pakistan
```

---

##  Application Info

### Prerequisites

- Python 3.10+
- MySQL Server + MySQL Workbench
- pip

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/byteofhoney/Electronic-Voting-System-Pakistan.git
cd Electronic-Voting-System-Pakistan

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Linux/Mac
venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install flask mysql-connector-python python-dotenv werkzeug

# 4. Set up your .env file
cp .env.example .env
# Edit .env with your MySQL credentials and a secret key
```

### Environment Variables

Create a `.env` file in the project root:

```
SECRET_KEY=secretkey
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=mysqlpassword
DB_NAME=electronic_voting_system
```

### Database Setup

```bash
# Run the schema file in MySQL Workbench or CLI
mysql -u root -p < electronic_voting_system.sql
```

The schema file creates all 24 tables, foreign key constraints, and seeds:
- 5 provinces (Punjab, Sindh, Balochistan, KPK, Islamabad)
- 9 cities
- 21 NA constituencies, 22 PA constituencies
- 5 major political parties (PTI, PMLN, PPP, MQM, JUI-F)
- One default ECP admin account

#### Default ECP Admin Login

```
Username: ecp_admin
Password: admin123
```

#### Run the Application

```bash
python main.py
```

Visit `http://127.0.0.1:5000` — you will be redirected to the login page.

---

### Complete Voting Flow

```
ECP creates election (date + start/end times)
        ↓
Provincial Officer imports voters (CSV) + adds candidates + creates stations
        ↓
Provincial Officer creates Polling Officers and assigns them to stations
        ↓
Election status auto-transitions: Upcoming → Active (at start time)
        ↓
Voter arrives at polling machine
        ↓
Voter enters name + CNIC
        ↓
Voter enters "pending" queue → waits on screen
        ↓
Polling Officer sees queue → approves or rejects voter
        ↓
Voter's screen auto-detects approval (JS polling every 2s)
        ↓
Voter is redirected to ballot → selects NA + PA candidates
        ↓
Vote recorded anonymously (ballot_token, no link to voter identity)
        ↓
voters.has_voted = 1 → voter_status.status = 'voted'
        ↓
Machine resets for next voter
        ↓
Election auto-closes at end time → ECP views final results
```

---

### Security Features

**CNIC Validation:** Custom `cnic_utils.py` decodes the first 2 digits of any Pakistani CNIC to determine the holder's province and division. Used during voter import and voter login to prevent cross-station voting.

**7-Check Voter Login:** Before a voter enters the queue, the system verifies: station exists → station active → CNIC format valid → voter registered → not already voted → voter's city matches station's city → active election exists.

**Double-Vote Lock:** Two independent checks at vote-cast time — `voters.has_voted` flag AND `voter_status.status` — must both pass before any vote is written.

**Ballot Anonymity:** Votes are stored with a `ballot_token` (64-char cryptographic hex). No column in `votes_na` or `votes_pa` references a voter CNIC or identity.

**Role-Scoped Access:** Provincial Officers can only access their own province's data. Polling Officers can only see their station's queue. Cross-scope access is blocked at the application layer.

**Audit Trail:** Every admin action (create election, add officer, import voters, close election) is written to the `audit_trail` table with actor, role, action, affected table, and timestamp.

**Login Logging:** Every login attempt (success or failure) is recorded in `login_logs` with username, role, IP address, and timestamp.

---

### Database Schema

24 tables across 6 groups:

| Group | Tables |
|---|---|
| Roles & Users | `ecp_admin`, `provincial_officers`, `polling_officers`, `voters`, `user_roles` |
| Locations | `provinces`, `cities`, `constituencies_na`, `constituencies_pa` |
| Election Setup | `elections`, `polling_stations`, `station_assignments`, `election_schedule` |
| Parties & Candidates | `parties`, `candidates`, `candidate_na`, `candidate_pa` |
| Voting & Ballots | `ballots`, `votes_na`, `votes_pa`, `voter_status` |
| Logs & Security | `login_logs`, `audit_trail`, `failed_attempts` |

---

### Key Features by Role

#### ECP Admin
- Create and manage elections with start/end schedule
- Auto-status transition: Upcoming → Active → Closed
- Live election countdown timer on dashboard
- Manage political parties (logo upload, activate/deactivate)
- Create Provincial Officers (auto-generated passwords)
- View real-time results: party seat tally, constituency breakdowns, voter turnout
- Full audit trail and login log viewer
- Direct management of Islamabad Capital Territory (no Provincial Officer)
- CSV voter import for ICT with CNIC province validation

#### Provincial Officer
- Province-scoped dashboard (stats for their province only)
- Import voters via CSV with full validation
- Add and manage candidates (photo upload, NA/PA constituency assignment)
- Create polling stations
- Create and manage Polling Officers (password reset, activate/deactivate)
- Export audit trail CSV

#### Polling Officer
- Real-time voter approval queue
- Approve or reject pending voters
- Station-scoped view (only sees their own station's queue)

#### Voter
- CNIC + name authentication at the polling machine
- Real-time waiting screen (no page refresh)
- Dual ballot: National Assembly + Provincial Assembly
- Anonymous vote recording
- Immediate confirmation screen with anonymity notice

---

### 🇵🇰 Pakistan Specific Implementation

- **Dual constituency system:** Every voter has both an NA and PA constituency (except Islamabad, which has NA only). Ballots show both.
- **CNIC prefix map:** Covers all Pakistani divisions — KPK (11–17), Punjab (31–38), Sindh (41–45), Balochistan (51–56), Islamabad (61).
- **Real constituency data:** Seeded with actual NA and PA constituency numbers (NA-118 Lahore-I, PP-140, NA-48 Islamabad-I, etc.).
- **Islamabad special case:** ICT is a federal territory — ECP manages it directly without a Provincial Officer.
- **Bilingual UI:** Login and voter-facing pages include Urdu text (Noto Nastaliq Urdu font) alongside English.
- **Party data:** Seeded with PTI, PMLN, PPP, MQM, JUI-F with official abbreviations and party colors.

---

## 🗳️ How to Run an Election!

This section explains the exact operational sequence from first login to final results.

---

### ◆ Step 1:-  ECP Admin: Initial Setup

Log in at:
```
http://127.0.0.1:5000/login
Username: ecp_admin
```

From the ECP dashboard:
1. Go to **Parties** → add political parties and upload logos
2. Go to **Officers** → create Provincial Officers (system generates their passwords)
3. Tell each Provincial Officer their username and generated password

---

### ◆ Step 2:-  Provincial Officer: Province Setup

Each Provincial Officer logs in and sets up their province:

**Add Polling Stations** (`/provincial/stations`)
- Create each physical polling station with name, address, city

**Find the station IDs** — after creating stations, check their IDs:
```sql
SELECT station_id, station_name, address, city_id
FROM polling_stations
WHERE province_id = <your_province_id>
ORDER BY station_id;
```

**Add Polling Officers** (`/provincial/polling-officers`)
- Create one Polling Officer per station
- Assign them to their station during creation
- Note down the generated password for each

**Add Candidates** (`/provincial/candidates`)
- Add each candidate with photo, party, and NA/PA constituency

**Import Voters** (`/provincial/import-voters`)
- Upload a CSV file with columns: `cnic, full_name, date_of_birth, gender, city_id, constituency_na_id`
- The system validates each CNIC's province prefix and rejects mismatches

---

### ◆ Step 3:-  ECP Admin: Create the Election

Go to **Dashboard** → scroll to the election panel → fill in:
- Election name
- Election date
- Start time and end time

The election starts as `Upcoming` and auto transitions to `Active` at the scheduled start time. It auto closes at the end time but you can also close it manually from the dashboard.

---

### ◆ Step 4:-  Set Up the Voting Machines

Each polling station needs a dedicated device (PC/tablet) set to the voter login URL for that station.

First, find the station ID:
```sql
SELECT station_id, station_name FROM polling_stations;
```

Then open the browser on the voting machine and navigate to:
```
http://127.0.0.1:5000/vote/login?station_id=<station_id>
```

For example, if Station "F-8 Community Centre" has `station_id = 7`:
```
http://127.0.0.1:5000/vote/login?station_id=7
```

Set this as the browser's homepage or bookmark it. The machine stays on this URL all time after each vote the screen resets back to it automatically.

---

### ◆ Step 5:-  Polling Officer: Log In on a Separate Device

The Polling Officer logs in on their own device which is different from the voting machine:
```
http://127.0.0.1:5000/login
```

After login they are taken directly to:
```
http://127.0.0.1:5000/polling-officer/verify
```

This page shows a real time queue of voters waiting for approval **from their station only**. No voters from other stations appear here.

---

### ◆ Step 6:- Voter Casts a Vote

On the voting machine:

1. Voter enters their **Full Name** and **CNIC** (format: `XXXXX-XXXXXXX-X`, e.g. `35202-1234567-1`)
2. System runs 7 validation checks silently
3. If all pass, voter sees: *"Please Wait — The Polling Officer is verifying your identity"*
4. The Polling Officer's screen shows the voter's name in the queue
5. Polling Officer clicks **Approve** or Reject
6. The voter's screen detects approval automatically and checks every 2 seconds, no refresh needed
7. Voter is redirected to the ballot and selects one candidate for NA and one for PA
8. Vote is submitted, voter sees *"شکریہ — Thank You"* confirmation
9. Screen resets to the login page for the next voter

---

### ◆ Step 7:-  ECP Admin: View Results

Go to **Results** (`/ecp/results`):
- Summary: total votes cast, voter turnout %, NA seats active, PA seats active
- Party seat tally: which party is leading in how many NA and PA seats
- Per constituency breakdown filterable by province and seat type (NA / PA)

Results are live during an Active election and final once Closed.

To verify vote counts directly in the database:

```sql
-- Total votes cast
SELECT COUNT(*) FROM votes_na;
SELECT COUNT(*) FROM votes_pa;

-- Votes per candidate in a specific NA seat
SELECT c.full_name, p.party_name, COUNT(v.vote_id) AS votes
FROM votes_na v
JOIN candidates c ON v.candidate_id = c.candidate_id
JOIN parties p ON c.party_id = p.party_id
WHERE v.na_id = <na_id> AND v.election_id = <election_id>
GROUP BY c.candidate_id
ORDER BY votes DESC;

-- Voter turnout
SELECT
    COUNT(*) AS total_registered,
    SUM(has_voted) AS total_voted,
    ROUND(SUM(has_voted) * 100.0 / COUNT(*), 2) AS turnout_pct
FROM voters;
```


<p align="center">◆ ◇ ◆ ◇ ◆ ◇ ◆ ◇ ◆ ◇ ◆</p>


