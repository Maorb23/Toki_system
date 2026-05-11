from django.core.management.base import BaseCommand
from comms.models import Organization, OrgValue, Team, Employee

VALUES = [
    ("Clarity over complexity", "Prefer simple, direct communication that makes the point easy to understand."),
    ("Ownership and accountability", "Make owners, next steps, and responsibilities explicit."),
    ("Respectful disagreement", "Disagree directly but professionally, with reasoning and respect."),
    ("Bias toward action", "Move work forward with practical next steps."),
    ("Customer impact", "Connect decisions and updates to customer outcomes when relevant."),
    ("Data-informed decisions", "Separate facts, assumptions, and opinions."),
    ("Make the ask explicit", "Every request should include the requested action and timing."),
    ("Escalate early when blocked", "Raise blockers early and clearly."),
]

TEAMS = {
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
}

EMPLOYEES = [
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
]

class Command(BaseCommand):
    help = "Seed a pseudo organization for the communication POC."

    def handle(self, *args, **options):
        org, _ = Organization.objects.get_or_create(
            name="Northstar Labs",
            defaults={"description": "A pseudo B2B SaaS organization used for receiver-aware communication testing."},
        )

        for name, description in VALUES:
            OrgValue.objects.get_or_create(
                organization=org,
                name=name,
                defaults={"description": description},
            )

        teams = {}
        for team_name, data in TEAMS.items():
            team, _ = Team.objects.get_or_create(
                organization=org,
                name=team_name,
                defaults={"description": data["description"], "norms": data["norms"]},
            )
            if team.norms != data["norms"]:
                team.norms = data["norms"]
                team.save(update_fields=["norms"])
            teams[team_name] = team

        employees_by_name = {}
        for item in EMPLOYEES:
            employee, _ = Employee.objects.get_or_create(
                organization=org,
                name=item["name"],
                defaults={
                    "team": teams[item["team"]],
                    "role": item["role"],
                    "seniority_level": item["seniority"],
                    "communication_preferences": item["prefs"],
                    "pain_points": item["pain"],
                    "receiver_prompt": item["prompt"],
                },
            )
            employee.team = teams[item["team"]]
            employee.role = item["role"]
            employee.seniority_level = item["seniority"]
            employee.communication_preferences = item["prefs"]
            employee.pain_points = item["pain"]
            employee.receiver_prompt = item["prompt"]
            employee.save()
            employees_by_name[item["name"]] = employee

        for item in EMPLOYEES:
            manager_name = item["manager"]
            if manager_name:
                employee = employees_by_name[item["name"]]
                employee.manager = employees_by_name[manager_name]
                employee.save(update_fields=["manager"])

        self.stdout.write(self.style.SUCCESS("Seeded pseudo organization: Northstar Labs"))
