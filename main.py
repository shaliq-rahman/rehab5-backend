from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import razorpay
import os
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from dotenv import load_dotenv
from datetime import date, timedelta

# Load environment variables
load_dotenv()

# Database Setup
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")
if not SQLALCHEMY_DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Booking Model
class Booking(Base):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, index=True)
    time = Column(String)
    name = Column(String)
    email = Column(String)
    phone = Column(String)
    order_id = Column(String, unique=True, index=True)
    status = Column(String, default="Pending")

Base.metadata.create_all(bind=engine)

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Razorpay Client (Use placeholders if env vars not set)
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_PLACEHOLDER")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "PLACEHOLDER_SECRET")
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

class OrderRequest(BaseModel):
    amount: int
    currency: str = "INR"
    receipt: str = "receipt#1"
    date: str
    slot: str
    name: str
    email: str
    phone: str

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/")
def read_root():
    return {"Hello": "World"}

@app.get("/slots")
def get_slots(date: str = Query(None, description="Date in YYYY-MM-DD format"), db: Session = Depends(get_db)):
    if not date:
        date = "Today"
    
    # Define all possible slots
    all_slots = ["09:00 AM", "10:00 AM", "11:00 AM", "02:00 PM", "03:00 PM", "04:00 PM"]
    
    # Fetch booked slots from DB for the given date
    booked_slots = db.query(Booking.time).filter(Booking.date == date).all()
    booked_times = [slot[0] for slot in booked_slots]
    
    
    # Filter available slots
    # available_slots = [slot for slot in all_slots if slot not in booked_times]
    
    slots_with_status = []
    for slot in all_slots:
        slots_with_status.append({
            "time": slot,
            "booked": slot in booked_times
        })
    
    return [
        {
            "date": date,
            "slots": slots_with_status
        }
    ]

@app.get("/next-availability")
def get_next_availability(db: Session = Depends(get_db)):
    """Returns the next slot that is not already booked, starting from today."""
    all_slots = ["09:00 AM", "10:00 AM", "11:00 AM", "02:00 PM", "03:00 PM", "04:00 PM"]

    # Day display helpers
    def friendly_date(d: date) -> str:
        today = date.today()
        if d == today:
            return "Today"
        elif d == today + timedelta(days=1):
            return "Tomorrow"
        else:
            return d.strftime("%d %b")  # e.g. "22 Feb"

    for day_offset in range(7):
        check_date = date.today() + timedelta(days=day_offset)
        date_str = check_date.strftime("%Y-%m-%d")

        booked = db.query(Booking.time).filter(Booking.date == date_str).all()
        booked_times = {b[0] for b in booked}

        for slot in all_slots:
            if slot not in booked_times:
                return {
                    "date": date_str,
                    "time": slot,
                    "display": f"{friendly_date(check_date)}, {slot}"
                }

    return {"date": None, "time": None, "display": "No availability this week"}

@app.post("/create-order")
def create_order(order: OrderRequest, db: Session = Depends(get_db)):
    try:
        # Check if slot is already booked
        existing_booking = db.query(Booking).filter(Booking.date == order.date, Booking.time == order.slot).first()
        if existing_booking:
             raise HTTPException(status_code=400, detail="Slot already booked")

        data = {
            "amount": order.amount,
            "currency": order.currency,
            "receipt": order.receipt,
            "payment_capture": 1
        }
        payment = razorpay_client.order.create(data=data)
        
        # Save booking to DB as Pending
        new_booking = Booking(
            date=order.date,
            time=order.slot,
            name=order.name,
            email=order.email,
            phone=order.phone,
            order_id=payment['id'] if 'id' in payment else "order_mock_123",
            status="Pending"
        )
        db.add(new_booking)
        db.commit()
        
        return payment
    except Exception as e:
        # Fallback for demo/invalid keys
        if "Unauthorized" in str(e) or "Badge" in str(e):
             mock_order_id = f"order_mock_{order.date}_{order.slot}".replace(" ", "_").replace("-", "")
             # Save mock booking
             new_booking = Booking(
                date=order.date,
                time=order.slot,
                name=order.name,
                email=order.email,
                phone=order.phone,
                order_id=mock_order_id,
                status="Pending"
            )
             db.add(new_booking)
             db.commit()

             return {
                "id": mock_order_id,
                "entity": "order",
                "amount": order.amount,
                "amount_paid": 0,
                "amount_due": order.amount,
                "currency": "INR",
                "receipt": order.receipt,
                "status": "created",
                "attempts": 0,
                "notes": [],
                "created_at": 1234567890
            }
from utils import send_email, create_calendar_event, generate_meet_link

class PaymentVerification(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    date: str
    slot: str
    email: str

@app.post("/verify-payment")
def verify_payment(data: PaymentVerification, db: Session = Depends(get_db)):
    try:
        # Verify Signature
        params_dict = {
            'razorpay_order_id': data.razorpay_order_id,
            'razorpay_payment_id': data.razorpay_payment_id,
        }
        razorpay_client.utility.verify_payment_signature({
            **params_dict,
            'razorpay_signature': data.razorpay_signature
        })

        # Update Booking Status
        booking = db.query(Booking).filter(Booking.order_id == data.razorpay_order_id).first()
        if booking:
            booking.status = "Confirmed"
            booking.order_id = data.razorpay_order_id # Ensure it matches
            db.commit()
            
            # Generate Google Meet link (creates calendar event + returns Meet URL)
            meet_link = generate_meet_link(data.date, data.slot)
            
            # Send confirmation email with Meet link
            subject = "Your Rehab 5 Booking is Confirmed ✅"
            body = f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Booking Confirmed</title>
</head>
<body style="margin:0;padding:0;background-color:#f0f4f8;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f4f8;padding:40px 0;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">

          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,#0f766e,#14b8a6);padding:40px 40px 32px;text-align:center;">
              <div style="display:inline-block;background:rgba(255,255,255,0.15);border-radius:12px;padding:10px 24px;margin-bottom:16px;">
                <span style="color:#ffffff;font-size:22px;font-weight:800;letter-spacing:2px;">REHAB 5</span>
              </div>
              <h1 style="color:#ffffff;margin:0;font-size:26px;font-weight:700;letter-spacing:0.5px;">Booking Confirmed</h1>
              <p style="color:rgba(255,255,255,0.85);margin:8px 0 0;font-size:14px;">Your consultation has been successfully scheduled</p>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:40px;">

              <p style="color:#374151;font-size:15px;margin:0 0 24px;">Hi there 👋,<br/><br/>
              Great news! Your appointment with <strong>Rehab 5</strong> has been confirmed. We look forward to seeing you.</p>

              <!-- Details Card -->
              <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border-radius:12px;border:1px solid #e2e8f0;margin-bottom:28px;">
                <tr>
                  <td style="padding:20px 24px;border-bottom:1px solid #e2e8f0;">
                    <table width="100%">
                      <tr>
                        <td style="color:#6b7280;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:1px;width:40%;">📅 Date</td>
                        <td style="color:#111827;font-size:15px;font-weight:600;">{data.date}</td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr>
                  <td style="padding:20px 24px;border-bottom:1px solid #e2e8f0;">
                    <table width="100%">
                      <tr>
                        <td style="color:#6b7280;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:1px;width:40%;">🕐 Time</td>
                        <td style="color:#111827;font-size:15px;font-weight:600;">{data.slot} (IST)</td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr>
                  <td style="padding:20px 24px;">
                    <table width="100%">
                      <tr>
                        <td style="color:#6b7280;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:1px;width:40%;">🎥 Meeting</td>
                        <td style="color:#111827;font-size:15px;font-weight:600;">Online Video Call</td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

              <!-- CTA Button -->
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td align="center" style="padding:8px 0 32px;">
                    <a href="{meet_link}" target="_blank"
                       style="display:inline-block;background:linear-gradient(135deg,#0f766e,#14b8a6);color:#ffffff;text-decoration:none;font-size:16px;font-weight:700;padding:16px 40px;border-radius:50px;letter-spacing:0.5px;box-shadow:0 4px 14px rgba(20,184,166,0.4);">
                      🎥 &nbsp; Join Video Consultation
                    </a>
                  </td>
                </tr>
              </table>

              <p style="color:#6b7280;font-size:13px;text-align:center;margin:0 0 8px;">Or copy this link to your browser:</p>
              <p style="color:#0f766e;font-size:13px;text-align:center;word-break:break-all;margin:0 0 32px;">{meet_link}</p>

              <!-- Reminder -->
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="background:#fffbeb;border-left:4px solid #f59e0b;border-radius:0 8px 8px 0;padding:14px 18px;">
                    <p style="margin:0;color:#92400e;font-size:13px;">⏰ <strong>Reminder:</strong> Please join the meeting 5 minutes before your scheduled time.</p>
                  </td>
                </tr>
              </table>

            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#f8fafc;padding:24px 40px;border-top:1px solid #e2e8f0;text-align:center;">
              <p style="color:#9ca3af;font-size:12px;margin:0 0 4px;">© 2025 Rehab 5. All rights reserved.</p>
              <p style="color:#9ca3af;font-size:12px;margin:0;">If you need to reschedule, please contact us at <a href="mailto:contactsoocher@gmail.com" style="color:#14b8a6;text-decoration:none;">contactsoocher@gmail.com</a></p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
            email_sent = send_email(data.email, subject, body)
            
            return {"status": "success", "meet_link": meet_link, "email_sent": email_sent}
        else:
             raise HTTPException(status_code=404, detail="Booking not found")

    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid payment signature")
    except Exception as e:
        print(f"Verification failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
