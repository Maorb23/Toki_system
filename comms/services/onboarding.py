from comms.models import Employee
from comms.services.event_log import log_event


def ensure_employee_onboarding(employee: Employee) -> bool:
    if employee.receiver_prompt.strip():
        return False

    team_name = employee.team.name if employee.team else "No team assigned"
    values = [
        value.name
        for value in employee.organization.values.all().order_by("name")[:6]
    ]
    values_text = ", ".join(values) if values else "the organization's values"

    employee.receiver_prompt = (
        f"{employee.name} is a {employee.role} on {team_name}. "
        f"Seniority: {employee.seniority_level or 'not specified'}. "
        f"Adapt messages to this receiver by being clear, respectful, and specific. "
        f"Align communication with: {values_text}."
    )
    employee.save(update_fields=["receiver_prompt", "updated_at"])
    log_event(
        "employee.onboarded",
        organization=employee.organization,
        receiver=employee,
        payload={
            "employee_id": employee.id,
            "team": team_name,
            "seniority_level": employee.seniority_level,
        },
    )
    return True
