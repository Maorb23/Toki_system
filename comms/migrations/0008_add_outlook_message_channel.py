from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("comms", "0007_organizationcontext_meetingcontext_projectcontext"),
    ]

    operations = [
        migrations.AlterField(
            model_name="message",
            name="channel",
            field=models.CharField(
                choices=[
                    ("slack", "Slack"),
                    ("teams", "Teams"),
                    ("gmail", "Gmail"),
                    ("outlook", "Outlook"),
                    ("email", "Email"),
                ],
                max_length=30,
            ),
        ),
    ]
