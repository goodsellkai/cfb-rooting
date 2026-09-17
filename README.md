# cfbroot

Simulates the rest of the college football season and shows which games this week help your team.

## Setup

```
python -m venv .venv
.venv\Scripts\python -m pip install -e .
```

Get a free API key at https://collegefootballdata.com/key and put it in a file named `.env` in this folder:

```
CFBD_API_KEY=your_key_here
```

## Run

Double-click `Start cfbroot.bat`, or run:

```
.venv\Scripts\cfbroot serve
```

Then open http://127.0.0.1:8000.

From the command line:

```
.venv\Scripts\cfbroot guide --team Michigan
```

Massey ratings for the current season:

```
.venv\Scripts\cfbroot massey
```

## Tests

```
.venv\Scripts\python -m pytest
```
