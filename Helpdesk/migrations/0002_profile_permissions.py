from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('Helpdesk', '0001_initial'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='profile',
            options={
                'verbose_name_plural': 'Perfiles',
                'permissions': [
                    ('register_agent', 'Can register new agent'),
                    ('manage_user_status', 'Can activate or deactivate a user'),
                ],
            },
        ),
    ]
