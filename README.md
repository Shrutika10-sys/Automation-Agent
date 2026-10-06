# Gemini Gems extractor- Phase 1

Read-only Playwright automation that opens your existing Chrome profile, visits the Gemini Gem Manager, and saves every Gem under **My Gems** and **Shared with me**.

For each Gem it finds that Gem's own Edit (pencil) button, opens the editor, and reads:

- Name
- Description
- Instructions

It appends every successfully retrieved Gem to the persistent `output/gems.csv`.

Premade by Google Gems are skipped. The script does not sign in, and it does not create, edit, share, or delete Gems.

## Architecture

```
main.py            Runs the loop, logs a summary, continues after a failed Gem
config.py          Loads .env and checks that the Chrome profile exists
browser.py         Launches your existing Chrome profile
gem_extractor.py   Finds My Gems and Shared with me, clicks each card's Edit button, reads the editor
file_manager.py    Appends every Gem to one persistent CSV across runs
```

Flow:

```
Existing Chrome profile
        ↓
Gemini Gem Manager (https://gemini.google.com/gems/view)
        ↓
Discover cards inside My Gems and Shared with me
        ↓
For each Gem:
    Find the Edit button inside that card
        ↓
    Click Edit
        ↓
    Read Name, Description, and Instructions
        ↓
        Append a row to output/gems.csv
        ↓
    Return to the Gem Manager
        ↓
Next Gem
```

The card on the manager page does not contain the full instructions. Those are read only after Edit opens the editor.

## Prerequisites

- Windows
- Python 3.11 or newer
- Google Chrome installed
- A Chrome profile that is already signed in to Gemini
- Gemini's interface language set to English (the script looks for "My Gems", "Edit", "Name", "Description", and "Instructions")

No API key is required. The script does not call an LLM.

## Installation

From this project folder:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

The script uses the Chrome already installed on your PC (`channel="chrome"`). A separate Playwright browser download is not required.

If Chrome fails to start because the Playwright driver is missing, run:

```powershell
python -m playwright install
```

## Find your Chrome User Data directory

1. Open Chrome.
2. Go to `chrome://version`.
3. Copy **Profile Path**.

Example:

```
C:\Users\<USERNAME>\AppData\Local\Google\Chrome\User Data\Profile 1
```

- `CHROME_USER_DATA_DIR` is the parent folder: `C:\Users\<USERNAME>\AppData\Local\Google\Chrome\User Data`
- `CHROME_PROFILE` is only the last folder: `Profile 1`

Use `Default` when the Profile Path ends in `Default`. Do not paste the full Profile Path into `CHROME_PROFILE`.

## Configure .env

```powershell
copy .env.example .env
```

Edit `.env`:

```
CHROME_USER_DATA_DIR=C:\Users\<USERNAME>\AppData\Local\Google\Chrome\User Data
CHROME_PROFILE=Profile 1
GEMS_URL=https://gemini.google.com/gems/view
OUTPUT_DIR=output
LOG_DIR=logs
HEADLESS=false
DEBUG=false
NAVIGATION_TIMEOUT_MS=60000
ELEMENT_TIMEOUT_MS=20000
```

`HEADLESS` defaults to `false` so you can watch the browser. Set `DEBUG=true` to keep extra logging and, when a Gem fails, save a screenshot plus a structural snapshot under `logs/failures/`. Snapshots list element roles and labels. They do not save cookies, passwords, or tokens.

The script checks that both the User Data folder and the profile folder exist before it opens Chrome.

## Leave Chrome open

Chrome encrypts the Gemini login for the normal Chrome window. A second window can show the same account name and still ask you to sign in.

1. Open Chrome with the profile in `.env`.
2. Go to `chrome://inspect/#remote-debugging`.
3. Turn remote debugging on.
4. Leave that Chrome window open.
5. Run the script. If Chrome asks to allow debugging, click Allow.

The script uses that open window. It closes only the tab it created.

## Run

```powershell
.venv\Scripts\activate
python main.py
```

Watch the browser. You should see the Gem Manager, then each Gem's editor, then a return to the manager. The script does not click Save, Update, Share, or Delete.

At the end it prints:

```
Total Gems: 5
Successful: 4
Failed: 1
Cumulative rows in CSV: 20
```

Failed Gems are listed with a reason. One failure does not stop the rest.

## Output

```
output/
└── gems.csv
logs/
└── automation.log
```

Every run appends to the same `output/gems.csv`; it does not create a new run folder or CSV. The `Sl No.` value continues from the last saved Gem, and the summary reports the cumulative number of CSV rows. A row is written only after that Gem's Name, Description, and Instructions have all been read. If a Gem is retrieved again on a later run, it is appended as another row rather than deduplicated.

On the first run after upgrading, if `output/gems.csv` does not exist or is empty, the script imports records from legacy CSV files in `output/run N/` folders. It supports both the previous per-Gem `Name,Description,Instructions` format and the previous run-level four-column format. Legacy files are left unchanged; once the central CSV has been initialized, migration is not repeated.

Instructions are copied as they appear, including line breaks. They are not summarized or rewritten.

The file is UTF-8 and is written with Python's `csv` module. Columns:

```
Sl No.,Gem Name,Description,Instructions
```

`Sl No.` starts at 1 and counts the records in the central CSV, including migrated records and Gems retrieved in earlier runs. Commas, quotes, Unicode, and multiline Instructions stay inside that Gem's single row. A failed Gem does not add a row. The Gem name inside the file is unchanged.

## Error handling

If one Gem cannot be read, the script:

1. Writes the error to `logs/automation.log`
2. Saves a screenshot and a DOM snapshot when `DEBUG=true`
3. Returns to the Gem Manager
4. Continues with the next Gem

The run exits with code `1` when any Gem fails or when Chrome cannot be started. It exits with code `0` when every discovered Gem is saved. An empty My Gems list is reported as zero Gems.

## Troubleshooting

**Chrome is not open, or remote debugging is off**

Open the profile from `.env`, go to `chrome://inspect/#remote-debugging`, turn remote debugging on, and leave Chrome open. Run the script again. Click Allow if Chrome asks.

**The wrong Google account opens**

`CHROME_PROFILE` does not match the Profile Path on `chrome://version`. Change the profile folder name in `.env`. The script checks `chrome://version` after it attaches and stops if a different profile folder is open.

**Gemini asks you to sign in**

That happens when the script starts its own Chrome window. Current Chrome keeps the Gemini cookies inside the original window. Use the open window with remote debugging turned on. The script does not type a username or password.

**My Gems is missing, or every Edit button fails**

Set `DEBUG=true` and run again. Inspect `logs/automation.log` and `logs/failures/`. The page layout may have changed, or the Gemini language may not be English.

**The script cannot attach**

Confirm remote debugging is on at `chrome://inspect/#remote-debugging` in the same Chrome profile, then run the script while that window is still open.

**A Gem is saved but the editor shows a leave dialog**

The script chooses Discard / Leave and does not click Save or Update. Your Gems are not modified.

**CSV looks wrong in Excel**

The file is UTF-8. In Excel, use Data → From Text/CSV and choose UTF-8 if a double-clicked file shows the wrong characters.

## Safety

This project only opens Gemini, reads My Gems, and writes local files. It does not copy cookies, read passwords, or send Gem contents to an API.
