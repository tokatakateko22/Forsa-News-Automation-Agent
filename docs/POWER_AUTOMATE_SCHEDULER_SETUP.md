# Power Automate Scheduled Trigger Setup Guide

This guide walks you through setting up a **Scheduled Cloud Flow** in Microsoft Power Automate to reliably trigger the **Weekly Financial News Digest** workflow at the exact time, with zero delay.

---

## 1. Prerequisites: GitHub Personal Access Token (PAT)

To allow Power Automate to trigger GitHub Actions, you need a Personal Access Token:

1. In GitHub, click your profile icon (top right) → **Settings**.
2. Scroll down on the left sidebar to **Developer settings** → **Personal access tokens** → **Tokens (classic)** (or **Fine-grained tokens**).
3. Click **Generate new token**:
   - **Token name:** `Power Automate Forsa Scheduler`
   - **Expiration:** Choose your preferred expiration (e.g. 90 days, 1 year, or No expiration).
   - **Scopes:** Select `repo` (Full control of private repositories) or `workflow` (Update GitHub Action workflows).
4. Click **Generate token** and copy the token string (`ghp_...`).

---

## 2. Create the Power Automate Scheduled Flow

1. Go to [make.powerautomate.com](https://make.powerautomate.com) and sign in with your corporate Microsoft 365 account.
2. In the left navigation, click **Create** → select **Scheduled cloud flow**.
3. Configure the flow dialog:
   - **Flow name:** `Forsa Weekly News Scheduler`
   - **Starting:** Today's date (`2026-09-20`)
   - **At these hours / minutes:** Set to **`15:00`** (3:00 PM) for today's test.
   - **Repeat every:** `1 Week` (or `1 Day` for daily test).
4. Click **Create**.

---

## 3. Configure the Recurrence Trigger

Click on the **Recurrence** trigger block to open its parameters:

### For Today's Test (15:00 PM Cairo):
- **Interval:** `1`
- **Frequency:** `Day`
- Click **Show advanced options**:
  - **Time zone:** `(UTC+02:00) Cairo` (or `(UTC+03:00) Cairo`)
  - **At these hours:** `15`
  - **At these minutes:** `0`

### For Weekly Production (Sundays at 08:30 AM Cairo):
- **Interval:** `1`
- **Frequency:** `Week`
- **On these days:** `Sunday`
- **Time zone:** `(UTC+02:00) Cairo` (or `(UTC+03:00) Cairo`)
- **At these hours:** `8`
- **At these minutes:** `30`

---

## 4. Add the HTTP Action to Dispatch GitHub Actions

1. Click **+ New step** below the Recurrence trigger.
2. Search for **HTTP** and select the standard **HTTP** action (Green globe icon).
3. Fill in the parameters exactly as follows:

| Field | Value |
| :--- | :--- |
| **Method** | `POST` |
| **URI** | `https://api.github.com/repos/tokatakateko22/Forsa-News-Automation-Agent/actions/workflows/weekly_news.yml/dispatches` |
| **Headers** | (See below) |
| **Body** | (See below) |

### Headers:
| Key | Value |
| :--- | :--- |
| `Accept` | `application/vnd.github.v3+json` |
| `Authorization` | `Bearer <PASTE_YOUR_GITHUB_PAT_HERE>` |
| `User-Agent` | `PowerAutomate-ForsaScheduler` |

### Body (JSON):
```json
{
  "ref": "main",
  "inputs": {
    "lookback_days": "7",
    "ignore_sent": "false",
    "triggered_by": "power_automate_scheduler"
  }
}
```

> [!TIP]
> If you want today's 15:00 test to bypass previously sent articles, you can temporarily set `"ignore_sent": "true"` in the body. For production runs, keep it `"false"`.

4. Click **Save** (top right).

---

## 5. Testing & Verification

1. In Power Automate, click **Test** (top right) → choose **Manually** → click **Test** → click **Run flow**.
2. Go to your repository's [Actions Tab](https://github.com/tokatakateko22/Forsa-News-Automation-Agent/actions).
3. You will see a new run of **Weekly Financial News Digest** appear within **1 to 3 seconds** with status `in_progress`.
4. Click on the run to view the summary:
   - **Trigger Event:** `workflow_dispatch`
   - **Source:** `power_automate_scheduler`
   - **Lookback Days:** `7`
