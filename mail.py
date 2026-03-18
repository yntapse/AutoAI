import win32com.client

# List of recipients and their names
recipients = [
    {'email': 'yashtapse8@gmail.com', 'name': 'Alice'},
]

# Create Outlook application object
outlook = win32com.client.Dispatch("Outlook.Application")

for recipient in recipients:
    mail = outlook.CreateItem(0)  # 0: Mail item
    mail.To = recipient['email']
    mail.Subject = f"Hello {recipient['name']}!"
    mail.Body = f"Dear {recipient['name']},\n\nThis is a personalized message.\n\nBest regards,\nYour Name"
    mail.Send()
    print(f"Email sent to {recipient['name']} at {recipient['email']}")