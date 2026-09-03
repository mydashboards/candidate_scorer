# Alex Candidate Scorer

Local Chrome extension for fast LinkedIn sourcing decisions using Ollama.

## Current role

EGYM Senior C++ Software Engineer

Core signal:
- 3-5+ years substantial C++
- application-layer / business-logic-heavy development

Nice-to-have only:
- Qt / QML
- CMake

Hard skip when the profile is predominantly ECU / device-controller / firmware / low-level hardware work without strong application-layer depth.

## One-time setup on Mac

Open Terminal and run:

```bash
cd ~/Documents
git clone https://github.com/mydashboards/candidate_scorer.git
cd candidate_scorer/server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Make sure Ollama is installed and `qwen3:4b` exists:

```bash
ollama list
```

If needed:

```bash
ollama pull qwen3:4b
```

## Start the scorer

From Terminal:

```bash
cd ~/Documents/candidate_scorer/server
source .venv/bin/activate
uvicorn app:app --reload --port 3847
```

Leave that Terminal window open while sourcing.

## Load the Chrome extension

1. Open `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked**
4. Select:
   `~/Documents/candidate_scorer/extension`
5. Open a LinkedIn profile
6. Click the Alex Candidate Scorer extension icon
7. Click **Analyze**

## Future updates

When the GitHub version changes:

```bash
cd ~/Documents/candidate_scorer
git pull
```

Then reload the extension in `chrome://extensions` if extension files changed. The FastAPI server automatically reloads backend changes when it is running with `--reload`.
