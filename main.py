from fastapi import FastAPI, HTTPException, Query, Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import razorpay
import os
import json
import urllib.request
import jwt
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float
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

# JWT Auth Secrets
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-super-secret-key-that-should-be-changed")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "password123")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="admin/login")

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_current_admin(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None or username != ADMIN_USER:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
    return username

# UAE Standard Time = UTC+4
UAE_OFFSET = timedelta(hours=4)
AED_CONSULTATION_FEE = 250  # AED — shown to user

# ── Real-time exchange rate cache (1-hour TTL) ──
_rate_cache: dict = {"rate": 23.0, "fetched_at": None}
EXCHANGE_RATE_TTL = timedelta(hours=1)
EXCHANGE_RATE_API = "https://open.er-api.com/v6/latest/AED"

def get_aed_to_inr_rate() -> float:
    """Fetch real-time AED→INR rate from open.er-api.com with 1-hour cache."""
    global _rate_cache
    now = datetime.utcnow()
    if _rate_cache["fetched_at"] is None or now - _rate_cache["fetched_at"] > EXCHANGE_RATE_TTL:
        try:
            with urllib.request.urlopen(EXCHANGE_RATE_API, timeout=5) as resp:
                data = json.loads(resp.read())
            if data.get("result") == "success":
                rate = data["rates"].get("INR", 23.0)
                _rate_cache = {"rate": round(rate, 4), "fetched_at": now}
                print(f"[exchange-rate] Fetched live AED→INR: {_rate_cache['rate']}")
        except Exception as e:
            print(f"[exchange-rate] Fetch failed, using cached rate {_rate_cache['rate']}: {e}")
    return _rate_cache["rate"]


def get_uae_time():
    return datetime.utcnow() + UAE_OFFSET

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
    amount = Column(Integer, default=0)           # legacy, kept for compat
    amount_aed = Column(Float, default=0.0)       # AED amount shown to user
    amount_inr = Column(Float, default=0.0)       # INR amount charged via Razorpay
    created_at = Column(DateTime, default=datetime.utcnow)  # always UTC

class SystemSettings(Base):
    __tablename__ = "system_settings"
    id = Column(Integer, primary_key=True, index=True)
    doctor_email = Column(String, default="shaliqrhmnv@gmail.com")

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
    amount_aed: float = AED_CONSULTATION_FEE  # AED fee; backend converts to INR paise
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


@app.post("/admin/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    if form_data.username != ADMIN_USER or form_data.password != ADMIN_PASS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": ADMIN_USER}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

class SettingsUpdate(BaseModel):
    doctor_email: str

@app.get("/admin/settings")
def get_settings(db: Session = Depends(get_db), current_admin: str = Depends(get_current_admin)):
    settings = db.query(SystemSettings).first()
    if not settings:
        settings = SystemSettings(doctor_email="shaliqrhmnv@gmail.com")
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings

@app.put("/admin/settings")
def update_settings(data: SettingsUpdate, db: Session = Depends(get_db), current_admin: str = Depends(get_current_admin)):
    settings = db.query(SystemSettings).first()
    if not settings:
        settings = SystemSettings(doctor_email=data.doctor_email)
        db.add(settings)
    else:
        settings.doctor_email = data.doctor_email
    db.commit()
    db.refresh(settings)
    return settings

@app.get("/admin/bookings")
def get_all_bookings(
    date: str = Query(None, description="Filter by date in YYYY-MM-DD format"),
    db: Session = Depends(get_db), 
    current_admin: str = Depends(get_current_admin)
):
    query = db.query(Booking)
    if date:
        query = query.filter(Booking.date == date)
        
    bookings = query.order_by(Booking.id.desc()).all()
    return bookings

@app.post("/admin/resend-email/{booking_id}")
async def resend_booking_email(booking_id: int, db: Session = Depends(get_db), current_admin: str = Depends(get_current_admin)):
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    from utils import generate_meet_link, get_doctor_email_template, get_patient_email_template, send_emails_concurrently
    
    settings = db.query(SystemSettings).first()
    doctor_email = settings.doctor_email if settings and settings.doctor_email else "shaliqrhmnv@gmail.com"
    
    meet_link = generate_meet_link(booking.date, booking.time, attendee_emails=[booking.email, doctor_email])
    
    subject = "Your Rehab 5 Booking is Confirmed"
    body = get_patient_email_template(booking.name, booking.date, booking.time, meet_link)
    
    doctor_subject = "Patient Consultation Resent"
    doctor_body = get_doctor_email_template(
        booking_date=booking.date, 
        booking_time=booking.time, 
        patient_name=booking.name, 
        patient_email=booking.email, 
        patient_phone=booking.phone, 
        meet_link=meet_link
    )
    
    email_sent = await send_emails_concurrently(
        patient_email=booking.email,
        patient_subject=subject,
        patient_body=body,
        doctor_email=doctor_email,
        doctor_subject=doctor_subject,
        doctor_body=doctor_body
    )
    
    if not email_sent:
         raise HTTPException(status_code=500, detail="Failed to send email via SMTP.")

    return {"status": "success", "message": "Email resent successfully."}

@app.get("/slots")
def get_slots(date_str: str = Query(None, alias="date", description="Date in YYYY-MM-DD format"), db: Session = Depends(get_db)):
    if not date_str:
        date_str = date.today().strftime("%Y-%m-%d")
    
    # Define all possible slots (UAE business hours, GST/UTC+4)
    all_slots = ["09:00 AM", "10:00 AM", "11:00 AM", "02:00 PM", "03:00 PM", "04:00 PM"]
    
    # Fetch booked slots from DB for the given date (ONLY Confirmed ones)
    booked_slots = db.query(Booking.time).filter(
        Booking.date == date_str, 
        Booking.status == "Confirmed"
    ).all()
    booked_times = {slot[0] for slot in booked_slots}
    
    # Current time in UAE (UTC+4) for comparison
    now_uae = get_uae_time()
    today_str = now_uae.strftime("%Y-%m-%d")
    
    slots_with_status = []
    for slot_time_str in all_slots:
        is_booked = slot_time_str in booked_times
        is_passed = False
        
        if date_str == today_str:
            # Parse slot time (e.g., "09:00 AM")
            slot_dt = datetime.strptime(slot_time_str, "%I:%M %p")
            # Create a full datetime for today in UAE time
            slot_full_dt = now_uae.replace(hour=slot_dt.hour, minute=slot_dt.minute, second=0, microsecond=0)
            if now_uae > slot_full_dt:
                is_passed = True
        
        slots_with_status.append({
            "time": slot_time_str,
            "booked": is_booked,
            "passed": is_passed
        })
    
    return [
        {
            "date": date_str,
            "slots": slots_with_status
        }
    ]

@app.get("/next-availability")
def get_next_availability(db: Session = Depends(get_db)):
    """Returns the next slot that is not already booked, starting from today (UAE time)."""
    all_slots = ["09:00 AM", "10:00 AM", "11:00 AM", "02:00 PM", "03:00 PM", "04:00 PM"]
    now_uae = get_uae_time()
    today_str = now_uae.strftime("%Y-%m-%d")

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

        booked = db.query(Booking.time).filter(
            Booking.date == date_str,
            Booking.status == "Confirmed"
        ).all()
        booked_times = {b[0] for b in booked}

        for slot_time_str in all_slots:
            if slot_time_str not in booked_times:
                # Check if it has passed if it's today (UAE time)
                if date_str == today_str:
                    slot_dt = datetime.strptime(slot_time_str, "%I:%M %p")
                    slot_full_dt = now_uae.replace(hour=slot_dt.hour, minute=slot_dt.minute, second=0, microsecond=0)
                    if now_uae > slot_full_dt:
                        continue  # Skip passed slot

                return {
                    "date": date_str,
                    "time": slot_time_str,
                    "display": f"{friendly_date(check_date)}, {slot_time_str}"
                }

    return {"date": None, "time": None, "display": "No availability this week"}

@app.post("/create-order")
def create_order(order: OrderRequest, db: Session = Depends(get_db)):
    aed_fee = order.amount_aed if order.amount_aed > 0 else AED_CONSULTATION_FEE
    aed_fils = int(aed_fee * 100)                    # Razorpay needs smallest subunit
    
    # Internal tracking: calculate INR equivalent
    live_rate = get_aed_to_inr_rate()
    inr_amount = round(aed_fee * live_rate, 2)

    try:
        # Check if slot is already booked (Confirmed status only)
        existing_booking = db.query(Booking).filter(
            Booking.date == order.date, 
            Booking.time == order.slot,
            Booking.status == "Confirmed"
        ).first()
        if existing_booking:
             raise HTTPException(status_code=400, detail="Slot already booked")

        data = {
            "amount": aed_fils,
            "currency": "AED",
            "receipt": order.receipt,
            "payment_capture": 1
        }
        payment = razorpay_client.order.create(data=data)
        
        # Save booking to DB as Pending with both AED and INR amounts
        new_booking = Booking(
            date=order.date,
            time=order.slot,
            name=order.name,
            email=order.email,
            phone=order.phone,
            order_id=payment['id'] if 'id' in payment else "order_mock_123",
            amount=int(inr_amount),     # legacy field (INR)
            amount_aed=aed_fee,
            amount_inr=inr_amount,
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
                amount=int(inr_amount),
                amount_aed=aed_fee,
                amount_inr=inr_amount,
                status="Pending"
            )
             db.add(new_booking)
             db.commit()

             return {
                "id": mock_order_id,
                "entity": "order",
                "amount": aed_fils,
                "amount_paid": 0,
                "amount_due": aed_fils,
                "currency": "AED",
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
async def verify_payment(data: PaymentVerification, db: Session = Depends(get_db)):
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
            from utils import generate_meet_link, get_patient_email_template, get_doctor_email_template, send_emails_concurrently
            
            settings = db.query(SystemSettings).first()
            doctor_email = settings.doctor_email if settings and settings.doctor_email else "shaliqrhmnv@gmail.com"
            meet_link = generate_meet_link(data.date, data.slot, attendee_emails=[data.email, doctor_email])
            
            # Send confirmation email with Meet link
            subject = "Your Rehab 5 Booking is Confirmed"
            body = get_patient_email_template(booking.name, data.date, data.slot, meet_link)
            
            doctor_subject = "New Patient Consultation Scheduled"
            doctor_body = get_doctor_email_template(
                booking_date=data.date, 
                booking_time=data.slot, 
                patient_name=booking.name, 
                patient_email=data.email, 
                patient_phone=booking.phone, 
                meet_link=meet_link
            )
            
            email_sent = await send_emails_concurrently(
                patient_email=data.email,
                patient_subject=subject,
                patient_body=body,
                doctor_email=doctor_email,
                doctor_subject=doctor_subject,
                doctor_body=doctor_body
            )
            
            if not email_sent:
                # Still return success for payment if email fails
                print("Failed to dispatch emails concurrently during payment verification.")
                
            return {"status": "success", "meet_link": meet_link, "email_sent": email_sent}
        else:
             raise HTTPException(status_code=404, detail="Booking not found")

    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid payment signature")
    except Exception as e:
        print(f"Verification failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
