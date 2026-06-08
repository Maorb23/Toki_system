from django.core.management.base import BaseCommand
from comms.models import (
    Employee,
    MeetingContext,
    OrgValue,
    Organization,
    OrganizationContext,
    ProjectContext,
    Team,
)


NORTHSTAR_LABS = {
    "name": "Northstar Labs",
    "description": "A pseudo B2B SaaS organization used for receiver-aware communication testing.",
    "values": [
        ("Clarity over complexity", "Prefer simple, direct communication that makes the point easy to understand."),
        ("Ownership and accountability", "Make owners, next steps, and responsibilities explicit."),
        ("Respectful disagreement", "Disagree directly but professionally, with reasoning and respect."),
        ("Bias toward action", "Move work forward with practical next steps."),
        ("Customer impact", "Connect decisions and updates to customer outcomes when relevant."),
        ("Data-informed decisions", "Separate facts, assumptions, and opinions."),
        ("Make the ask explicit", "Every request should include the requested action and timing."),
        ("Escalate early when blocked", "Raise blockers early and clearly."),
    ],
    "teams": {
        "Executive": {
            "description": "Company leadership and strategy.",
            "norms": ["Lead with the decision or risk.", "Keep updates concise.", "Separate facts from assumptions."],
        },
        "Product": {
            "description": "Product strategy, roadmap, and customer needs.",
            "norms": ["Explain customer impact.", "Clarify tradeoffs.", "Make decisions explicit."],
        },
        "Engineering": {
            "description": "Software delivery, architecture, and reliability.",
            "norms": ["Be precise.", "Include technical context when needed.", "Flag blockers early."],
        },
        "Sales": {
            "description": "Revenue, prospects, and customer conversations.",
            "norms": ["Focus on customer pain.", "Keep asks concrete.", "Use business impact."],
        },
        "Customer Success": {
            "description": "Customer health, adoption, and escalations.",
            "norms": ["Show empathy.", "Clarify next steps.", "Highlight customer risk."],
        },
        "People": {
            "description": "HR, culture, hiring, and internal support.",
            "norms": ["Use respectful framing.", "Avoid ambiguity.", "Consider sensitivity."],
        },
        "Operations": {
            "description": "Internal processes, finance, vendors, and company operations.",
            "norms": ["Be structured.", "Make owners clear.", "Surface dependencies."],
        },
    },
    "employees": [
        {
            "name": "Ari Cohen", "role": "CEO", "team": "Executive", "manager": None, "seniority": "Executive",
            "prefs": {"style": "concise, decision-oriented", "detail": "low unless risk is high", "structure": "decision, risk, ask"},
            "pain": ["long updates without a decision", "unclear ownership"],
            "prompt": "Ari prefers concise executive communication. Lead with the decision, risk, or ask. Use bullets and make ownership explicit.",
        },
        {
            "name": "Maya Levi", "role": "VP Product", "team": "Product", "manager": "Ari Cohen", "seniority": "Executive",
            "prefs": {"style": "strategic and customer-focused", "detail": "medium", "structure": "context, customer impact, tradeoff, ask"},
            "pain": ["solutions without customer context", "unclear prioritization"],
            "prompt": "Maya wants customer context and tradeoffs before decisions. Be clear about impact and what decision is needed.",
        },
        {
            "name": "Noam Bar", "role": "VP Engineering", "team": "Engineering", "manager": "Ari Cohen", "seniority": "Executive",
            "prefs": {"style": "direct and technical", "detail": "high for engineering issues", "structure": "problem, cause, options, recommendation"},
            "pain": ["vague technical claims", "surprises late in delivery"],
            "prompt": "Noam prefers direct, technically precise updates. Include blockers, root cause, options, and recommended next step.",
        },
        {
            "name": "Dana Weiss", "role": "Backend Engineer", "team": "Engineering", "manager": "Noam Bar", "seniority": "Senior IC",
            "prefs": {"style": "direct but respectful", "detail": "high", "structure": "context, exact ask, deadline"},
            "pain": ["unclear requirements", "urgent requests without reason"],
            "prompt": "Dana responds well to clear context, exact technical asks, and why timing matters. Avoid vague urgency.",
        },
        {
            "name": "Omer Katz", "role": "Frontend Engineer", "team": "Engineering", "manager": "Noam Bar", "seniority": "IC",
            "prefs": {"style": "collaborative", "detail": "medium", "structure": "goal, user impact, implementation note"},
            "pain": ["design changes without user rationale", "unclear acceptance criteria"],
            "prompt": "Omer prefers collaborative messages with user rationale and clear acceptance criteria.",
        },
        {
            "name": "Lior Green", "role": "Product Manager", "team": "Product", "manager": "Maya Levi", "seniority": "Manager",
            "prefs": {"style": "structured", "detail": "medium", "structure": "background, decision, next steps"},
            "pain": ["ambiguous ownership", "unprioritized requests"],
            "prompt": "Lior likes structured communication with background, decisions, and owners. Prioritize asks clearly.",
        },
        {
            "name": "Tamar Rosen", "role": "Head of Sales", "team": "Sales", "manager": "Ari Cohen", "seniority": "Executive",
            "prefs": {"style": "business-focused", "detail": "low-medium", "structure": "customer, revenue impact, ask"},
            "pain": ["internal jargon", "slow decisions"],
            "prompt": "Tamar prefers business-focused communication. Lead with customer/revenue impact and make the ask concrete.",
        },
        {
            "name": "Gil Amir", "role": "Account Executive", "team": "Sales", "manager": "Tamar Rosen", "seniority": "IC",
            "prefs": {"style": "action-oriented", "detail": "low", "structure": "opportunity, obstacle, needed action"},
            "pain": ["long internal debates", "unclear urgency"],
            "prompt": "Gil prefers short, action-oriented messages focused on opportunity, obstacle, and next action.",
        },
        {
            "name": "Rina Tal", "role": "Customer Success Manager", "team": "Customer Success", "manager": "Ari Cohen", "seniority": "Manager",
            "prefs": {"style": "empathetic and clear", "detail": "medium", "structure": "customer issue, impact, next step"},
            "pain": ["dismissive tone", "unclear commitments"],
            "prompt": "Rina values empathy and clarity. Include customer impact, commitments, and next steps.",
        },
        {
            "name": "Eli Mor", "role": "People Partner", "team": "People", "manager": "Ari Cohen", "seniority": "Manager",
            "prefs": {"style": "diplomatic", "detail": "medium", "structure": "context, sensitivity, proposed action"},
            "pain": ["blunt phrasing in sensitive topics", "lack of context"],
            "prompt": "Eli prefers diplomatic, context-aware communication, especially for sensitive people topics.",
        },
        {
            "name": "Yael Shalev", "role": "Operations Lead", "team": "Operations", "manager": "Ari Cohen", "seniority": "Manager",
            "prefs": {"style": "process-oriented", "detail": "medium-high", "structure": "issue, owner, timeline, dependency"},
            "pain": ["missing dependencies", "unclear owner"],
            "prompt": "Yael prefers structured messages that identify issue, owner, timeline, and dependencies.",
        },
    ],
    "context": {
        "operating_context": {
            "market": "Mid-market B2B SaaS for customer operations teams.",
            "stage": "Post-Series A growth with a Q3 enterprise-readiness push.",
            "planning_horizon": "Current quarter plus next-quarter roadmap planning.",
        },
        "current_priorities": [
            "Reduce enterprise onboarding friction before the Q3 roadmap review.",
            "Improve reliability and incident communication for strategic accounts.",
            "Clarify product tradeoffs between roadmap speed and customer-specific requests.",
        ],
        "communication_patterns": [
            "Use customer impact before solution detail when speaking with Product or Sales.",
            "Use exact owners and dates when asking Engineering or Operations for help.",
            "Escalations should include blocker, impact, options, and recommended next step.",
        ],
        "customer_segments": ["Enterprise CS leaders", "Scale-up operations teams", "Strategic design partners"],
        "known_constraints": [
            "Engineering capacity is tight until the reliability workstream closes.",
            "Sales needs clearer roadmap language for Q3 renewal conversations.",
            "Customer Success is tracking two accounts with high onboarding risk.",
        ],
    },
    "projects": [
        {
            "name": "Q3 Roadmap Alignment",
            "description": "Finalize enterprise roadmap tradeoffs and prepare a clear narrative for Sales and Customer Success.",
            "status": "active",
            "priority": "high",
            "quarter": "Q3",
            "team": "Product",
            "owner": "Maya Levi",
            "goals": ["Lock top-three roadmap bets", "Document tradeoffs", "Prepare customer-facing talking points"],
            "risks": ["Sales may overpromise roadmap timing", "Engineering estimates are still volatile"],
            "dependencies": ["Reliability hardening capacity", "Customer Success escalation themes"],
            "stakeholders": ["Ari Cohen", "Noam Bar", "Tamar Rosen", "Rina Tal"],
        },
        {
            "name": "Enterprise Onboarding Reliability",
            "description": "Stabilize onboarding workflows for strategic customers and reduce manual intervention.",
            "status": "active",
            "priority": "high",
            "quarter": "Q3",
            "team": "Engineering",
            "owner": "Noam Bar",
            "goals": ["Reduce failed onboarding jobs", "Clarify incident ownership", "Improve status visibility"],
            "risks": ["Backend queue fixes may slip", "Customer-facing timelines need careful framing"],
            "dependencies": ["Dana Weiss backend queue audit", "Rina Tal customer escalation notes"],
            "stakeholders": ["Dana Weiss", "Rina Tal", "Yael Shalev"],
        },
        {
            "name": "Strategic Account Escalation Playbook",
            "description": "Create a shared escalation pattern for customer risks, ownership, and response timing.",
            "status": "planned",
            "priority": "medium",
            "quarter": "Q3",
            "team": "Customer Success",
            "owner": "Rina Tal",
            "goals": ["Standardize escalation messages", "Clarify handoffs", "Reduce repeated customer context gathering"],
            "risks": ["Too much process could slow urgent replies"],
            "dependencies": ["Operations owner map", "Product roadmap language"],
            "stakeholders": ["Ari Cohen", "Maya Levi", "Yael Shalev"],
        },
    ],
    "meetings": [
        {
            "title": "Q3 Roadmap Review",
            "meeting_type": "roadmap",
            "cadence": "Weekly on Tuesday",
            "status": "recurring",
            "team": "Product",
            "owner": "Maya Levi",
            "participants": ["Maya Levi", "Noam Bar", "Tamar Rosen", "Rina Tal", "Ari Cohen"],
            "related_projects": ["Q3 Roadmap Alignment", "Enterprise Onboarding Reliability"],
            "summary": "Align roadmap scope, customer commitments, and tradeoffs before executive review.",
            "decisions": ["Separate committed Q3 items from exploratory discovery items."],
            "open_questions": ["Which reliability fixes must ship before Sales can discuss enterprise rollout?"],
            "action_items": [
                {"owner": "Maya Levi", "task": "Draft roadmap narrative with customer impact.", "status": "open"},
                {"owner": "Noam Bar", "task": "Confirm engineering confidence levels.", "status": "open"},
            ],
        },
        {
            "title": "Strategic Customer Risk Sync",
            "meeting_type": "customer_escalation",
            "cadence": "Twice weekly",
            "status": "recurring",
            "team": "Customer Success",
            "owner": "Rina Tal",
            "participants": ["Rina Tal", "Dana Weiss", "Yael Shalev", "Maya Levi"],
            "related_projects": ["Enterprise Onboarding Reliability", "Strategic Account Escalation Playbook"],
            "summary": "Review high-risk customer onboarding issues and agree on owners for next steps.",
            "decisions": ["Use a single owner per escalation thread."],
            "open_questions": ["Can Product provide clearer language for partial rollout delays?"],
            "action_items": [
                {"owner": "Dana Weiss", "task": "Summarize backend blockers in non-technical language.", "status": "open"},
                {"owner": "Yael Shalev", "task": "Confirm operational handoff owner.", "status": "open"},
            ],
        },
    ],
}


THE_OFFICE = {
    "name": "The Office",
    "description": "A Scranton branch-inspired pseudo organization with sales, accounting, operations, data science, and developer roles.",
    "values": [
        ("Start with the point", "Open with the decision, ask, or blocker before wandering into background."),
        ("Make work visible", "Name the owner, next step, deadline, and customer or branch impact."),
        ("Respect the room", "Keep humor and candor from turning into confusion, blame, or personal digs."),
        ("Confirm the ask", "Requests should say what is needed, by when, and what good looks like."),
        ("Use evidence, not volume", "Bring numbers, examples, and context instead of louder opinions."),
        ("Keep meetings useful", "Use written context for status and reserve meetings for decisions or alignment."),
        ("Protect customer trust", "Explain how a message affects clients, coworkers, or branch credibility."),
        ("Escalate without theater", "Raise risks early, calmly, and with a recommended next move."),
    ],
    "teams": {
        "Regional Management": {
            "description": "Branch leadership, priorities, and cross-team coordination.",
            "norms": ["Lead with branch impact.", "Keep decisions explicit.", "Translate chaos into next steps."],
        },
        "Sales": {
            "description": "Client relationships, pipeline, renewals, and account growth.",
            "norms": ["Connect asks to client outcomes.", "Keep urgency believable.", "Avoid internal jargon."],
        },
        "Reception & Office Admin": {
            "description": "Front desk, office coordination, scheduling, and internal routing.",
            "norms": ["Be clear about logistics.", "Surface dependencies.", "Confirm ownership."],
        },
        "Accounting": {
            "description": "Billing, finance controls, vendor payments, and reporting.",
            "norms": ["Use exact numbers.", "Do not bury exceptions.", "Separate facts from assumptions."],
        },
        "People & Compliance": {
            "description": "HR, policy, employee relations, and workplace risk.",
            "norms": ["Use neutral language.", "Document context.", "Avoid personal framing."],
        },
        "Warehouse & Operations": {
            "description": "Fulfillment, inventory, shipping, and branch operations.",
            "norms": ["Name blockers early.", "Clarify handoffs.", "Use practical timelines."],
        },
        "Data Science": {
            "description": "Forecasting, experiment analysis, customer segmentation, and operating metrics.",
            "norms": ["State confidence levels.", "Call out data limits.", "Tie findings to decisions."],
        },
        "Product Engineering": {
            "description": "Internal tools, automations, integrations, and developer support.",
            "norms": ["Include repro steps when relevant.", "Define acceptance criteria.", "Flag technical risk early."],
        },
    },
    "employees": [
        {
            "name": "Michael Scott", "role": "Regional Manager", "team": "Regional Management", "manager": None, "seniority": "Executive",
            "prefs": {"style": "affirming, concise, and concrete", "detail": "low-medium", "structure": "headline, why it matters, clear ask"},
            "pain": ["messages that feel cold", "unclear branch impact", "too much technical detail"],
            "prompt": "Michael needs a warm but focused message. Lead with the headline, explain branch impact simply, and make the ask concrete.",
        },
        {
            "name": "Dwight Schrute", "role": "Assistant Regional Manager", "team": "Sales", "manager": "Michael Scott", "seniority": "Manager",
            "prefs": {"style": "direct, rule-aware, operational", "detail": "high", "structure": "objective, authority, steps, deadline"},
            "pain": ["vague authority", "soft asks", "missing deadlines"],
            "prompt": "Dwight responds to direct, operational communication. State the objective, who owns it, the rule or reason, and the deadline.",
        },
        {
            "name": "Jim Halpert", "role": "Sales Lead", "team": "Sales", "manager": "Michael Scott", "seniority": "Senior IC",
            "prefs": {"style": "plainspoken and collaborative", "detail": "medium", "structure": "context, tradeoff, recommended next step"},
            "pain": ["overly formal messages", "performative urgency", "missing customer context"],
            "prompt": "Jim prefers plain, collaborative messages with enough context to act. Avoid inflated urgency and land on a practical next step.",
        },
        {
            "name": "Pam Beesly", "role": "Office Administrator", "team": "Reception & Office Admin", "manager": "Michael Scott", "seniority": "Manager",
            "prefs": {"style": "thoughtful and organized", "detail": "medium", "structure": "context, logistics, owner, timing"},
            "pain": ["last-minute ambiguity", "unclear handoffs", "tone that ignores effort"],
            "prompt": "Pam values organized, considerate communication. Clarify logistics, handoffs, timing, and acknowledge constraints when relevant.",
        },
        {
            "name": "Angela Martin", "role": "Accounting Manager", "team": "Accounting", "manager": "Michael Scott", "seniority": "Manager",
            "prefs": {"style": "precise, formal, policy-aware", "detail": "high", "structure": "facts, exception, required action"},
            "pain": ["imprecise numbers", "casual policy language", "missing accountability"],
            "prompt": "Angela needs precise facts, numbers, and accountable next steps. Keep tone formal and avoid vague phrasing.",
        },
        {
            "name": "Oscar Martinez", "role": "Senior Data Scientist", "team": "Data Science", "manager": "Michael Scott", "seniority": "Senior IC",
            "prefs": {"style": "analytical and calm", "detail": "high", "structure": "question, evidence, caveat, recommendation"},
            "pain": ["unsupported claims", "false certainty", "requests that ignore data limitations"],
            "prompt": "Oscar prefers analytical messages with evidence, caveats, and a clear recommendation. Separate data from interpretation.",
        },
        {
            "name": "Ryan Howard", "role": "Growth Data Scientist", "team": "Data Science", "manager": "Oscar Martinez", "seniority": "IC",
            "prefs": {"style": "strategic and metrics-driven", "detail": "medium", "structure": "goal, metric, experiment, ask"},
            "pain": ["unclear success metrics", "busywork without strategic purpose"],
            "prompt": "Ryan responds to messages that connect work to a goal, metric, experiment, or growth decision. Make the ask measurable.",
        },
        {
            "name": "Kelly Kapoor", "role": "Customer Insights Analyst", "team": "Data Science", "manager": "Oscar Martinez", "seniority": "IC",
            "prefs": {"style": "energetic but specific", "detail": "medium", "structure": "customer signal, insight, action"},
            "pain": ["dry messages without customer context", "unclear follow-up"],
            "prompt": "Kelly values energy and customer context, but still needs a specific insight and next action.",
        },
        {
            "name": "Andy Bernard", "role": "Frontend Developer", "team": "Product Engineering", "manager": "Michael Scott", "seniority": "IC",
            "prefs": {"style": "encouraging and structured", "detail": "medium", "structure": "user goal, acceptance criteria, timing"},
            "pain": ["negative tone", "unclear acceptance criteria", "surprise scope changes"],
            "prompt": "Andy works best with encouraging, structured messages. Include user goals, acceptance criteria, and timing.",
        },
        {
            "name": "Creed Bratton", "role": "Backend Developer", "team": "Product Engineering", "manager": "Michael Scott", "seniority": "Senior IC",
            "prefs": {"style": "short and concrete", "detail": "medium", "structure": "problem, system, requested fix"},
            "pain": ["abstract strategy", "long context", "unclear system names"],
            "prompt": "Creed needs short, concrete technical requests. Name the system, the problem, and the requested fix.",
        },
        {
            "name": "Erin Hannon", "role": "Junior Developer", "team": "Product Engineering", "manager": "Andy Bernard", "seniority": "Junior IC",
            "prefs": {"style": "kind, explicit, step-by-step", "detail": "high", "structure": "goal, steps, example, support path"},
            "pain": ["assumed context", "abrupt criticism", "missing examples"],
            "prompt": "Erin benefits from kind, explicit instructions with examples and a clear support path if she gets stuck.",
        },
        {
            "name": "Kevin Malone", "role": "Accounts Payable Specialist", "team": "Accounting", "manager": "Angela Martin", "seniority": "IC",
            "prefs": {"style": "simple and concrete", "detail": "low-medium", "structure": "what, when, where"},
            "pain": ["dense explanations", "multiple asks in one message"],
            "prompt": "Kevin needs simple, concrete messages. Use one ask at a time and make timing and location obvious.",
        },
        {
            "name": "Stanley Hudson", "role": "Senior Account Executive", "team": "Sales", "manager": "Dwight Schrute", "seniority": "Senior IC",
            "prefs": {"style": "brief and respectful", "detail": "low", "structure": "ask, reason, deadline"},
            "pain": ["unnecessary meetings", "dramatic urgency", "long preambles"],
            "prompt": "Stanley prefers brief, respectful messages. Get to the ask, give the reason, and avoid unnecessary drama.",
        },
        {
            "name": "Phyllis Vance", "role": "Account Executive", "team": "Sales", "manager": "Dwight Schrute", "seniority": "Senior IC",
            "prefs": {"style": "warm and practical", "detail": "medium", "structure": "customer, concern, next step"},
            "pain": ["cold tone", "unclear customer impact"],
            "prompt": "Phyllis responds to warm, practical messages that connect the issue to a customer and a clear next step.",
        },
        {
            "name": "Toby Flenderson", "role": "HR and Compliance Lead", "team": "People & Compliance", "manager": "Michael Scott", "seniority": "Manager",
            "prefs": {"style": "neutral, documented, policy-grounded", "detail": "high", "structure": "context, policy, recommendation"},
            "pain": ["personal attacks", "informal handling of sensitive topics"],
            "prompt": "Toby needs neutral, documented communication with policy context and a calm recommendation.",
        },
        {
            "name": "Darryl Philbin", "role": "Operations Lead", "team": "Warehouse & Operations", "manager": "Michael Scott", "seniority": "Manager",
            "prefs": {"style": "straightforward and realistic", "detail": "medium", "structure": "blocker, impact, needed decision"},
            "pain": ["plans that ignore operational reality", "unclear handoffs"],
            "prompt": "Darryl prefers straightforward messages that acknowledge operational constraints and name the decision needed.",
        },
        {
            "name": "Meredith Palmer", "role": "Supplier Integration Developer", "team": "Product Engineering", "manager": "Andy Bernard", "seniority": "IC",
            "prefs": {"style": "direct and low-friction", "detail": "medium", "structure": "issue, system, next action"},
            "pain": ["bureaucratic phrasing", "unclear ownership"],
            "prompt": "Meredith prefers direct, low-friction messages. Name the system issue and the immediate next action.",
        },
    ],
    "context": {
        "operating_context": {
            "market": "Regional paper sales branch with internal analytics and automation workstreams.",
            "stage": "Branch modernization while protecting existing customer relationships.",
            "planning_horizon": "Monthly branch targets plus current-quarter process improvements.",
        },
        "current_priorities": [
            "Improve branch forecast accuracy without adding unnecessary meetings.",
            "Stabilize supplier integration work for warehouse and sales handoffs.",
            "Create cleaner customer-risk communication between Sales, Accounting, and Operations.",
        ],
        "communication_patterns": [
            "Start with branch impact and the exact ask.",
            "Use evidence and confidence levels for analytics topics.",
            "Avoid vague urgency; name the customer, owner, and deadline.",
        ],
        "customer_segments": ["Regional paper buyers", "Renewal accounts", "Warehouse-dependent accounts"],
        "known_constraints": [
            "Sales has limited patience for long preambles.",
            "Accounting requires exact numbers and documented exceptions.",
            "Operations needs realistic timelines for supplier and warehouse changes.",
        ],
    },
    "projects": [
        {
            "name": "Branch Forecast Refresh",
            "description": "Update branch forecasting inputs and explain confidence levels for monthly planning.",
            "status": "active",
            "priority": "high",
            "quarter": "Current quarter",
            "team": "Data Science",
            "owner": "Oscar Martinez",
            "goals": ["Improve forecast accuracy", "Call out data limits", "Tie metrics to branch decisions"],
            "risks": ["Stakeholders may overread low-confidence segments", "Sales input quality varies by account"],
            "dependencies": ["Ryan Howard experiment readout", "Accounting exception data"],
            "stakeholders": ["Michael Scott", "Jim Halpert", "Angela Martin", "Dwight Schrute"],
        },
        {
            "name": "Supplier Portal Stabilization",
            "description": "Fix recurring supplier integration issues affecting warehouse handoffs and order visibility.",
            "status": "active",
            "priority": "high",
            "quarter": "Current quarter",
            "team": "Product Engineering",
            "owner": "Meredith Palmer",
            "goals": ["Reduce failed supplier syncs", "Clarify system ownership", "Document acceptance criteria"],
            "risks": ["Warehouse workarounds may mask integration failures", "Scope could expand without clear acceptance criteria"],
            "dependencies": ["Darryl Philbin operations feedback", "Creed Bratton backend fix"],
            "stakeholders": ["Darryl Philbin", "Andy Bernard", "Pam Beesly"],
        },
        {
            "name": "Customer Renewal Risk Review",
            "description": "Create a shared review path for renewal risks, customer concerns, and next actions.",
            "status": "planned",
            "priority": "medium",
            "quarter": "Current quarter",
            "team": "Sales",
            "owner": "Jim Halpert",
            "goals": ["Name renewal risks earlier", "Separate facts from assumptions", "Clarify who follows up"],
            "risks": ["Too much meeting time may reduce sales focus"],
            "dependencies": ["Branch Forecast Refresh", "Accounting exception summaries"],
            "stakeholders": ["Dwight Schrute", "Phyllis Vance", "Stanley Hudson", "Angela Martin"],
        },
    ],
    "meetings": [
        {
            "title": "Monday Branch Priorities",
            "meeting_type": "leadership_sync",
            "cadence": "Weekly on Monday",
            "status": "recurring",
            "team": "Regional Management",
            "owner": "Michael Scott",
            "participants": ["Michael Scott", "Dwight Schrute", "Jim Halpert", "Pam Beesly", "Darryl Philbin"],
            "related_projects": ["Customer Renewal Risk Review", "Supplier Portal Stabilization"],
            "summary": "Agree on branch priorities, customer risks, and owners for the week.",
            "decisions": ["Use written context for status before the meeting."],
            "open_questions": ["Which renewal accounts need manager involvement this week?"],
            "action_items": [
                {"owner": "Jim Halpert", "task": "Share top renewal risks in one page.", "status": "open"},
                {"owner": "Darryl Philbin", "task": "Confirm warehouse blockers affecting customer delivery.", "status": "open"},
            ],
        },
        {
            "title": "Forecast and Exceptions Review",
            "meeting_type": "analytics_review",
            "cadence": "Biweekly",
            "status": "recurring",
            "team": "Data Science",
            "owner": "Oscar Martinez",
            "participants": ["Oscar Martinez", "Ryan Howard", "Angela Martin", "Michael Scott"],
            "related_projects": ["Branch Forecast Refresh"],
            "summary": "Review forecast changes, exception data, and confidence levels before branch planning.",
            "decisions": ["Forecast summaries must include caveats and confidence levels."],
            "open_questions": ["Which accounting exceptions should be excluded from forecast training data?"],
            "action_items": [
                {"owner": "Oscar Martinez", "task": "Label low-confidence segments.", "status": "open"},
                {"owner": "Angela Martin", "task": "Provide documented billing exceptions.", "status": "open"},
            ],
        },
    ],
}


ORG_DATA = [NORTHSTAR_LABS, THE_OFFICE]


def seed_org(data: dict) -> Organization:
    org, _ = Organization.objects.get_or_create(
        name=data["name"],
        defaults={"description": data["description"]},
    )
    if org.description != data["description"]:
        org.description = data["description"]
        org.save(update_fields=["description"])

    for name, description in data["values"]:
        org_value, _ = OrgValue.objects.get_or_create(organization=org, name=name)
        if org_value.description != description:
            org_value.description = description
            org_value.save(update_fields=["description"])

    teams = {}
    for team_name, team_data in data["teams"].items():
        team, _ = Team.objects.get_or_create(organization=org, name=team_name)
        team.description = team_data["description"]
        team.norms = team_data["norms"]
        team.save(update_fields=["description", "norms"])
        teams[team_name] = team

    employees_by_name = {}
    for item in data["employees"]:
        employee, _ = Employee.objects.get_or_create(organization=org, name=item["name"])
        employee.team = teams[item["team"]]
        employee.role = item["role"]
        employee.seniority_level = item["seniority"]
        employee.communication_preferences = item["prefs"]
        employee.pain_points = item["pain"]
        employee.receiver_prompt = item["prompt"]
        employee.save()
        employees_by_name[item["name"]] = employee

    for item in data["employees"]:
        employee = employees_by_name[item["name"]]
        manager_name = item["manager"]
        employee.manager = employees_by_name[manager_name] if manager_name else None
        employee.save(update_fields=["manager"])

    context = data.get("context") or {}
    if context:
        org_context, _ = OrganizationContext.objects.get_or_create(organization=org)
        org_context.operating_context = context.get("operating_context", {})
        org_context.current_priorities = context.get("current_priorities", [])
        org_context.communication_patterns = context.get("communication_patterns", [])
        org_context.customer_segments = context.get("customer_segments", [])
        org_context.known_constraints = context.get("known_constraints", [])
        org_context.save()

    for item in data.get("projects") or []:
        project, _ = ProjectContext.objects.get_or_create(organization=org, name=item["name"])
        project.description = item.get("description", "")
        project.status = item.get("status", ProjectContext.Status.ACTIVE)
        project.priority = item.get("priority", "")
        project.quarter = item.get("quarter", "")
        project.team = teams.get(item.get("team"))
        project.owner = employees_by_name.get(item.get("owner"))
        project.goals = item.get("goals", [])
        project.risks = item.get("risks", [])
        project.dependencies = item.get("dependencies", [])
        project.stakeholders = item.get("stakeholders", [])
        project.save()

    for item in data.get("meetings") or []:
        meeting, _ = MeetingContext.objects.get_or_create(organization=org, title=item["title"])
        meeting.meeting_type = item.get("meeting_type", "")
        meeting.cadence = item.get("cadence", "")
        meeting.status = item.get("status", MeetingContext.Status.RECURRING)
        meeting.team = teams.get(item.get("team"))
        meeting.owner = employees_by_name.get(item.get("owner"))
        meeting.participants = item.get("participants", [])
        meeting.related_projects = item.get("related_projects", [])
        meeting.summary = item.get("summary", "")
        meeting.decisions = item.get("decisions", [])
        meeting.open_questions = item.get("open_questions", [])
        meeting.action_items = item.get("action_items", [])
        meeting.save()

    return org


class Command(BaseCommand):
    help = "Seed pseudo organizations for the communication POC."

    def handle(self, *args, **options):
        seeded = [seed_org(data).name for data in ORG_DATA]
        self.stdout.write(self.style.SUCCESS(f"Seeded pseudo organizations: {', '.join(seeded)}"))
