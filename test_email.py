import sys
import os
from dotenv import load_dotenv
load_dotenv()

from utils import send_email, get_doctor_email_template

try:
    doctor_body = get_doctor_email_template(
        booking_date="2025-02-25", 
        booking_time="10:00 AM", 
        patient_name="Test Patient", 
        patient_email="test@example.com", 
        patient_phone="1234567890", 
        meet_link="https://meet.google.com/abc-defg-hij"
    )
    result = send_email("shaliqrhmnv@gmail.com", "Test Doctor Email", doctor_body)
    print(f"Send email result: {result}")
except Exception as e:
    import traceback
    traceback.print_exc()
