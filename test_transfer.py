import os
import datetime
import uuid
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_FILE = 'token.json'
CALENDAR_SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/gmail.send',
]

def test_transfer():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, CALENDAR_SCOPES)
    service = build('calendar', 'v3', credentials=creds)

    # 1. Create a dummy event
    dt = datetime.datetime.utcnow() + datetime.timedelta(days=1)
    start_time = dt.isoformat() + 'Z'
    end_time = (dt + datetime.timedelta(hours=1)).isoformat() + 'Z'

    doctor_email = 'shaliqrhmnv@gmail.com' # Try to transfer to this

    event = {
        'summary': 'Test Transfer Event',
        'location': 'Online - Google Meet',
        'description': 'Testing ownership transfer.',
        'start': {'dateTime': start_time},
        'end': {'dateTime': end_time},
        'conferenceData': {
            'createRequest': {
                'requestId': uuid.uuid4().hex,
                'conferenceSolutionKey': {'type': 'hangoutsMeet'}
            }
        },
        'attendees': [{'email': doctor_email}],
    }

    print("Creating event...")
    event_result = service.events().insert(
        calendarId='primary',
        body=event,
        conferenceDataVersion=1
    ).execute()
    
    event_id = event_result['id']
    print(f"Created event ID: {event_id}")

    # 2. Try to move the event
    print(f"Attempting to move event to {doctor_email}...")
    try:
        moved_event = service.events().move(
            calendarId='primary',
            eventId=event_id,
            destination=doctor_email
        ).execute()
        print("Success! Event moved.")
        print(moved_event.get('organizer'))
    except Exception as e:
        print(f"Move failed: {e}")

if __name__ == '__main__':
    test_transfer()
