from django.core.management.base import BaseCommand
from comms.models import Organization, OrgValue, Team, Employee


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

    return org


class Command(BaseCommand):
    help = "Seed pseudo organizations for the communication POC."

    def handle(self, *args, **options):
        seeded = [seed_org(data).name for data in ORG_DATA]
        self.stdout.write(self.style.SUCCESS(f"Seeded pseudo organizations: {', '.join(seeded)}"))
