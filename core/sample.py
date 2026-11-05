"""Three sample meetings in different transcript formats (a weekly cadence, recurring topics)."""
from datetime import date, timedelta

SPRINT = """[00:00:05] Meera: Good morning everyone. Let's start with the dashboard launch and data quality.
[00:00:20] Rahul: Data quality is the main blocker. The south region numbers are still off in the dashboard.
[00:00:45] Alex: I checked the pipeline yesterday. The duplicate records come from the warehouse export, not from the dashboard logic.
[00:01:30] Meera: Okay. We decided to use the warehouse export as the single source of truth for the dashboard.
[00:01:50] Alex: I'll write a data quality report by Friday.
[00:02:10] Rahul: I will fix the duplicate records in the south region before next Tuesday.
[00:02:40] Meera: Priya, can you update the KPI definitions?
[00:02:48] Priya: Sure, I'll update the KPI definitions by Thursday.
[00:03:15] Rahul: What about the launch date? Are we still aiming for the 20th?
[00:03:30] Meera: Let's keep the launch date on the 20th. We agreed on that unless data quality slips again.
[00:04:00] Priya: One risk is that the finance team has not approved the revenue definition yet. That is still open and we need to follow up with them.
[00:04:30] Meera: Right. Meera to ask finance for sign-off by Wednesday. Anything else? No? Thanks everyone.
"""

CLIENT = """WEBVTT

1
00:00:03.000 --> 00:00:08.000
<v Dana>Thanks for joining. We reviewed the dashboard and overall the team is happy with the layout.</v>

2
00:00:09.000 --> 00:00:16.000
<v Alex>Great to hear. We still have some data quality work on the south region before the launch.</v>

3
00:00:17.000 --> 00:00:25.000
<v Dana>Our main request is a revenue drill-down by product. Can you add that before the launch?</v>

4
00:00:26.000 --> 00:00:30.000
<v Alex>Yes, we can add the revenue drill-down. I'll send the revised mockups by Monday.</v>

5
00:00:31.000 --> 00:00:40.000
<v Dana>Good. We agreed to keep the budget unchanged then. Dana will share the access credentials tomorrow.</v>

6
00:00:41.000 --> 00:00:50.000
<v Meera>What is the deadline for the final sign-off from your side?</v>

7
00:00:51.000 --> 00:00:58.000
<v Dana>We need the final dashboard by the 20th. That date is fixed on our side.</v>
"""

RETRO = """Meera: Let's do the retro. The dashboard launched on time which is great.
Rahul: The data quality checks saved us. Before the launch we caught the duplicate records early.
Priya: Reviews were slow this sprint. Code reviews often waited two days.
Meera: We decided to start a daily review slot at 3 PM so reviews do not pile up.
Alex: I'll set up the review slot calendar invite today.
Rahul: Documentation is still weak. We should document the data pipeline.
Meera: Agreed. Rahul to write the pipeline documentation by next Friday.
Priya: Should we also look at automated data quality alerts? That is not decided yet, let's revisit next sprint.
"""


def sample_meetings(today: date) -> list[dict]:
    return [
        {"title": "Sprint planning", "date": today - timedelta(days=14), "participants": ["Meera", "Rahul", "Alex", "Priya"], "file": "sprint_planning.txt", "text": SPRINT},
        {"title": "Client sync: Northwind", "date": today - timedelta(days=7), "participants": ["Dana", "Alex", "Meera"], "file": "client_sync.vtt", "text": CLIENT},
        {"title": "Sprint retro", "date": today - timedelta(days=1), "participants": ["Meera", "Rahul", "Priya", "Alex"], "file": "sprint_retro.txt", "text": RETRO},
    ]
