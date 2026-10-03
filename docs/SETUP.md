# Oura Setup Guide

Connect your Oura account to show your daily scores and last night's sleep on your board.

## Overview

The Oura plugin shows your daily Sleep, Readiness and Activity scores, last night's sleep and resting heart rate, and today's steps.

**Prerequisites:**

- An Oura Ring and an Oura account with an active Oura Membership (Oura does not share data through its API without one)
- FiestaBoard 9.8.0 or later
- About five minutes to register a free Oura API application

Oura limits an API application that it has not approved to 10 users, so each person registers their own. Oura retired personal access tokens in December 2025; signing in is the only way to connect.

## Quick Setup

### 1. Enable the Plugin

1. Open the FiestaBoard web UI and go to **Integrations**.
2. Find **Oura** and turn it on.
3. Open its settings. The **Account connection** section shows the steps below and the **redirect URI** your board uses, with a copy button.

### 2. Register an Oura API application

1. Go to [cloud.ouraring.com/oauth/applications](https://cloud.ouraring.com/oauth/applications) and sign in with your Oura account.
2. Create a new application. Fill in the fields Oura asks for (name, description, contact email and website links). For a personal application, your own name and email are fine.
3. For **Redirect URIs**, paste the redirect URI copied from the plugin's **Account connection** section. Copy it from there rather than typing it: Oura compares it exactly.
4. If Oura asks which scopes the application uses, include **Daily** (daily summaries of sleep, activity and readiness). The plugin asks for nothing else.
5. Save. Copy the application's **Client ID** and **Client Secret**.

### 3. Sign in

1. Back in the plugin's **Account connection** section, paste the **Client ID** and **Client Secret**.
2. Press **Sign in with Oura**. You are sent to Oura to approve access, then back to FiestaBoard.
3. On Oura's consent screen, leave **Daily** allowed. If you untick it, the plugin gets `403` and cannot show anything.
4. The section now shows **Connected**.

Your Client Secret stays on your board and is sent only to Oura's token endpoint.

### 4. Create a Board Template

Create a page with a template such as:

```
OURA
{{oura.sleep_tile}} SLEEP {{oura.sleep_score}}
{{oura.readiness_tile}} READINESS {{oura.readiness_score}}
{{oura.activity_tile}} ACTIVITY {{oura.activity_score}}
SLEPT {{oura.total_sleep}} RHR {{oura.resting_hr}}
STEPS {{oura.steps_text}}
```

### 5. View on Your Board

Activate the page. New data appears within the refresh interval (10 minutes by default). Sleep data reaches Oura when you open the Oura app in the morning, so the Sleep score can stay empty until then.

## Template Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{{oura.sleep_score}}` / `readiness_score` / `activity_score` | Daily scores, 0-100 | `82` |
| `{{oura.sleep_tile}}` / `readiness_tile` / `activity_tile` | Green 85+, yellow 70-84, red below 70 | `{65}` |
| `{{oura.sleep_label}}` / `readiness_label` / `activity_label` | `OPTIMAL`, `GOOD` or `PAY ATTENTION` | `GOOD` |
| `{{oura.scores_line}}` | All three scores on one line | `SLP 82 RDY 76 ACT 91` |
| `{{oura.total_sleep}}` | Total sleep last night | `7H 32M` |
| `{{oura.total_sleep_minutes}}` | Total sleep in minutes | `452` |
| `{{oura.time_in_bed}}` | Time in bed | `8H 5M` |
| `{{oura.deep_sleep}}` / `rem_sleep` | Sleep stages | `1H 30M` |
| `{{oura.sleep_efficiency}}` | Percent of time in bed asleep | `93` |
| `{{oura.resting_hr}}` | Lowest heart rate during sleep (bpm) | `52` |
| `{{oura.average_hr}}` | Average heart rate during sleep (bpm) | `58` |
| `{{oura.average_hrv}}` | Average HRV during sleep (ms) | `48` |
| `{{oura.bedtime}}` / `wake_time` | Sleep start and end, 24-hour | `22:48` |
| `{{oura.steps}}` / `steps_text` | Steps today | `8,423` |
| `{{oura.active_calories}}` / `total_calories` | Calories today | `412` |
| `{{oura.temperature_deviation}}` | Temperature deviation from baseline (°C) | `+0.2` |

## Configuration Reference

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| Enable Oura (`enabled`) | boolean | `false` | Turn the plugin on |
| Client ID (`client_id`) | string | | From your Oura API application |
| Client Secret (`client_secret`) | password | | From your Oura API application |
| Refresh Interval (`refresh_seconds`) | integer | `600` | Seconds between checks, 300-3600 |

This plugin uses no environment variables. Tokens are stored and refreshed by FiestaBoard.

## Troubleshooting

**"Not signed in to Oura"**
Open the plugin's settings and press **Sign in with Oura**. If it was connected before, Oura may have refused a token refresh (for example, after you revoked access): sign in again.

**The Sign in button is disabled**
Enter both the Client ID and the Client Secret first.

**Oura shows an error about the redirect URI**
The redirect URI in your Oura application must match the one in the plugin's **Account connection** section exactly. Copy it again from there and paste it into the application's Redirect URIs.

**"Oura rejected the sign-in (401)"**
Access was revoked or the sign-in expired. Press **Reconnect**.

**"Oura refused access (403)"**
Your Oura Membership may have expired, or **Daily** was not allowed on the consent screen. Renew the membership if needed, then press **Reconnect** and allow Daily.

**A score or last night's sleep is empty**
Oura has not calculated it yet. Sleep data syncs when you open the Oura app; open it and wait for the next refresh.

**"Oura rate limit reached"**
The plugin waits as long as Oura asks before trying again. Raise the refresh interval if it keeps happening.

**Another person wants to show their own data**
Add a second Oura plugin instance and sign them in there. Each person can use their own Oura application, or share yours (up to 10 users before Oura's approval).
