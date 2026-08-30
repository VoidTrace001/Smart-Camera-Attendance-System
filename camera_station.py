import cv2
import os
from camera import VideoCamera
from database import migrate_db, init_db

# ==============================================================================
# VERIVAULT AI - REMOTE CAMERA STATION
# ==============================================================================
# Instructions:
# 1. Set your DATABASE_URL environment variable to your Supabase URI.
#    Windows: $env:DATABASE_URL="your_supabase_uri"
# 2. Run: python camera_station.py
# ==============================================================================

def run_camera_station():
    print("==================================================")
    print("  VERIVAULT AI - SCANNING STATION (CLOUD SYNC)   ")
    print("==================================================")
    
    if not os.environ.get('DATABASE_URL'):
        print("[Error] DATABASE_URL not found!")
        print("Please set your Supabase Connection String as an environment variable.")
        return

    print("[System] Connecting to Supabase Cloud...")
    try:
        # Ensure cloud tables are ready
        init_db()
        migrate_db()
        print("[System] Cloud Database Sync: OK")
    except Exception as e:
        print(f"[Error] Could not connect to Cloud Database: {e}")
        return

    print("[System] Initializing Biometric Hardware...")
    cam = VideoCamera()
    cam.start_capture()
    
    print("[System] Station LIVE. Press 'q' to shut down.")

    while True:
        # Get frame from the hardware
        frame_bytes = cam.get_frame()
        
        if frame_bytes:
            # Convert bytes back to image for local display
            import numpy as np
            nparr = np.frombuffer(frame_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            # Show the "Live Intelligence" window
            cv2.imshow('VeriVault AI - Scanning Station', img)
        
        # Break loop on 'q' key
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    print("[System] Shutting down station...")
    cv2.destroyAllWindows()

if __name__ == "__main__":
    run_camera_station()
