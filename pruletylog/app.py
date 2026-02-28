import asyncio
import threading
import json
import csv
import os
import random
import socket
from datetime import datetime
from flask import Flask, request, render_template, jsonify, send_file
from flask_socketio import SocketIO
from http.server import HTTPServer, SimpleHTTPRequestHandler
from werkzeug.utils import secure_filename
import paho.mqtt.client as mqtt

# Flask + SocketIO setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-change-this'
socketio = SocketIO(app, cors_allowed_origins="*")

# Public API Flask app
public_app = Flask(__name__)
public_app.config['SECRET_KEY'] = 'public-api-key'
public_socketio = SocketIO(public_app, cors_allowed_origins="*")

# MQTT Configuration
MQTT_BROKER = "ip"
MQTT_PORT = 1883
MQTT_BASE_TOPIC = "prulety"
MQTT_USERNAME = "user"
MQTT_PASSWORD = "pass"

# Shared state
connected_devices = {}
admin_clients = []
device_last_data = {}  # Store last received data from each device
ota_servers = {}  # Active OTA HTTP servers {device_id: {'port': port, 'server': server, 'thread': thread}}
mqtt_client = None

# OTA firmware directory
FIRMWARE_DIR = 'ota_firmware'
os.makedirs(FIRMWARE_DIR, exist_ok=True)

# CSV logging
CSV_FILE = 'device_log.csv'
BIRDS_CSV_FILE = 'birds_log.csv'
csv_lock = threading.Lock()

def init_csv():
    """Initialize CSV file with headers if it doesn't exist"""
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'device_id', 'firmware', 'event_type', 'ssid', 'bssid', 'rssi', 'ip'])

def init_birds_csv():
    """Initialize birds CSV file with headers if it doesn't exist"""
    if not os.path.exists(BIRDS_CSV_FILE):
        with open(BIRDS_CSV_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'device_id', 'device_timestamp'])

def log_to_csv(device_id, firmware, event_type, ssid='', bssid='', rssi='', ip=''):
    """Log device event to CSV"""
    with csv_lock:
        with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            writer.writerow([timestamp, device_id, firmware, event_type, ssid, bssid, rssi, ip])

def log_bird_detection(device_id, device_timestamp):
    """Log bird detection to CSV"""
    with csv_lock:
        with open(BIRDS_CSV_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            writer.writerow([timestamp, device_id, device_timestamp])

# Initialize CSV on startup
init_csv()
init_birds_csv()

# Flask routes
@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/admin')
def admin():
    return render_template('dashboard.html')

@app.route('/api/devices')
def api_devices():
    online = {}
    for device_id, device_info in connected_devices.items():
        online[device_id] = {
            'firmware': device_info.get('firmware', 'unknown'),
            'status': 'online',
            'lastData': device_last_data.get(device_id, {})
        }

    # Offline devices - we can track these from CSV or maintain a separate list
    offline = {}

    return jsonify({'online': online, 'offline': offline})

@app.route('/api/download_csv')
def download_csv():
    return send_file(CSV_FILE, as_attachment=True, download_name='device_log.csv')

@app.route('/api/csv_data')
def csv_data():
    logs = []
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            logs = [','.join(row) for row in reader]
    return jsonify({'logs': logs})

@app.route('/api/birds_csv')
def download_birds_csv():
    return send_file(BIRDS_CSV_FILE, as_attachment=True, download_name='birds_log.csv')

@app.route('/api/birds_data')
def birds_data():
    logs = []
    if os.path.exists(BIRDS_CSV_FILE):
        with open(BIRDS_CSV_FILE, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader, None)  # Skip header
            for row in reader:
                logs.append(','.join(row))
    return jsonify({'logs': logs})

# OTA Functions
def find_free_port(start=40000, end=45000):
    """Find a free port in the specified range"""
    for port in range(start, end):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(('', port))
            s.close()
            return port
        except OSError:
            continue
    return None

def start_ota_http_server(device_id, firmware_path):
    """Start HTTP server to serve firmware file"""
    port = find_free_port()
    if not port:
        return None

    # Create custom handler that serves only the firmware file
    firmware_filename = os.path.basename(firmware_path)
    firmware_dir = os.path.dirname(firmware_path)

    class FirmwareHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=firmware_dir, **kwargs)

        def log_message(self, format, *args):
            print(f"[OTA Server {port}] {format % args}")

    def run_server():
        server = HTTPServer(('0.0.0.0', port), FirmwareHandler)
        ota_servers[device_id]['server'] = server
        print(f"[OTA] HTTP server started on port {port} for {device_id}")
        server.serve_forever()

    thread = threading.Thread(target=run_server, daemon=True)
    ota_servers[device_id] = {'port': port, 'server': None, 'thread': thread}
    thread.start()

    return port, firmware_filename

@app.route('/api/ota_upload', methods=['POST'])
def ota_upload():
    """Upload firmware and start OTA process"""
    if 'firmware' not in request.files:
        return jsonify({'success': False, 'error': 'No firmware file'})

    device_id = request.form.get('device_id')
    if not device_id or device_id not in connected_devices:
        return jsonify({'success': False, 'error': 'Device not connected'})

    firmware_file = request.files['firmware']
    if firmware_file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'})

    # Save firmware
    filename = secure_filename(firmware_file.filename)
    firmware_path = os.path.join(FIRMWARE_DIR, f"{device_id}_{filename}")
    firmware_file.save(firmware_path)

    # Start HTTP server
    result = start_ota_http_server(device_id, firmware_path)
    if not result:
        return jsonify({'success': False, 'error': 'Could not find free port'})

    port, firmware_filename = result

    # Get server IP (use first non-localhost IP)
    def get_local_ip():
        """Get the actual local IP address"""
        try:
            # Create a socket to find the local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Connect to an external address (doesn't actually send data)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            # Fallback to localhost if unable to determine
            return socket.gethostbyname(socket.gethostname())

    ip = get_local_ip()
    print(f"[OTA] Using server IP: {ip}")

    # Send OTA command to device via MQTT
    ota_url = f"http://{ip}:{port}/{firmware_filename}"

    send_ota_command(device_id, ota_url)

    return jsonify({'success': True, 'url': ota_url, 'port': port})

def send_ota_command(device_id, url):
    """Send OTA update command to device via MQTT"""
    if device_id in connected_devices:
        topic = f"{MQTT_BASE_TOPIC}/{device_id}/command"
        message = json.dumps({
            'type': 'ota_update',
            'url': url
        })
        mqtt_client.publish(topic, message)
        print(f"[OTA] Sent update command to {device_id}: {url}")

# Flask SocketIO handlers
@socketio.on('connect')
def handle_connect():
    print('Admin client connected')
    admin_clients.append(request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    print('Admin client disconnected')
    if request.sid in admin_clients:
        admin_clients.remove(request.sid)

@socketio.on('send_to_device')
def send_to_device(data):
    """Admin sends command to IoT device"""
    device_id = data.get('device_id')
    payload = data.get('payload')
    print(f"Admin sending to device {device_id}: {payload}")

    # Send to IoT device via MQTT
    if device_id in connected_devices:
        send_to_iot_device(device_id, payload)

def notify_admin(message):
    """Send notification to all connected admin clients"""
    socketio.emit('notification', message)

# Public API functions
def get_birds_stats():
    """Get bird detection statistics"""
    total_count = 0
    today_count = 0
    today = datetime.now().strftime('%Y-%m-%d')

    if os.path.exists(BIRDS_CSV_FILE):
        with csv_lock:
            with open(BIRDS_CSV_FILE, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader, None)  # Skip header
                for row in reader:
                    if len(row) >= 1:
                        total_count += 1
                        if row[0].startswith(today):
                            today_count += 1

    return today_count, total_count

def get_birds_history():
    """Get complete birds CSV history"""
    history = []
    if os.path.exists(BIRDS_CSV_FILE):
        with csv_lock:
            with open(BIRDS_CSV_FILE, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader, None)  # Skip header
                for row in reader:
                    if len(row) >= 3:
                        history.append({
                            'timestamp': row[0],
                            'device_id': row[1],
                            'device_timestamp': row[2]
                        })
    return history

# Public API Routes
@public_app.route('/')
def public_index():
    return jsonify({
        'status': 'ok',
        'message': 'Public Bird Detection API - Socket.IO only',
        'version': '1.0',
        'socketio': {
            'port': 4120,
            'events': {
                'connect': 'Připojit se k real-time detekcím - automaticky dostanete aktuální statistiky',
                'get_history': 'Vyžádat kompletní historii a statistiky',
                'get_stats': 'Vyžádat pouze statistiky',
                'bird_detection': 'Event: Real-time detekce ptáka (automaticky posílá server)'
            }
        },
        'documentation': 'https://github.com/yourproject/api-docs'
    })

# Public Socket.IO handlers
@public_socketio.on('connect')
def public_handle_connect():
    print('[Public API] Client connected')
    # Send current stats on connect
    today_count, total_count = get_birds_stats()
    public_socketio.emit('stats', {
        'prulety_dnes': today_count,
        'celkove_prulety': total_count
    })

@public_socketio.on('disconnect')
def public_handle_disconnect():
    print('[Public API] Client disconnected')

@public_socketio.on('get_stats')
def public_handle_get_stats():
    """Client requests only statistics"""
    today_count, total_count = get_birds_stats()
    public_socketio.emit('stats', {
        'prulety_dnes': today_count,
        'celkove_prulety': total_count
    })

@public_socketio.on('get_history')
def public_handle_get_history():
    """Client requests full history"""
    today_count, total_count = get_birds_stats()
    history = get_birds_history()

    public_socketio.emit('history', {
        'prulety_dnes': today_count,
        'celkove_prulety': total_count,
        'historie': history
    })

def notify_public_detection(device_id, timestamp):
    """Notify public API clients about new detection"""
    today_count, total_count = get_birds_stats()
    public_socketio.emit('bird_detection', {
        'device_id': device_id,
        'timestamp': timestamp,
        'prulety_dnes': today_count,
        'celkove_prulety': total_count
    })

# MQTT Handlers
def on_connect(client, userdata, flags, rc):
    """Callback when MQTT client connects to broker"""
    if rc == 0:
        print(f"[MQTT] Connected to broker at {MQTT_BROKER}:{MQTT_PORT}")
        # Subscribe to all device topics
        client.subscribe(f"{MQTT_BASE_TOPIC}/+/register")
        client.subscribe(f"{MQTT_BASE_TOPIC}/+/data")
        client.subscribe(f"{MQTT_BASE_TOPIC}/+/bird_detection")
        client.subscribe(f"{MQTT_BASE_TOPIC}/+/ota_progress")
        client.subscribe(f"{MQTT_BASE_TOPIC}/+/status")
        print(f"[MQTT] Subscribed to topics: {MQTT_BASE_TOPIC}/+/*")
    else:
        print(f"[MQTT] Connection failed with code {rc}")

def on_message(client, userdata, msg):
    """Callback when MQTT message is received"""
    try:
        topic_parts = msg.topic.split('/')
        if len(topic_parts) < 3:
            return

        device_id = topic_parts[1]
        message_type = topic_parts[2]

        data = json.loads(msg.payload.decode())

        # Handle registration
        if message_type == 'register':
            firmware = data.get('firmware', 'unknown')
            connected_devices[device_id] = {
                'firmware': firmware,
                'last_seen': datetime.now()
            }
            print(f"[MQTT] Device registered: {device_id} (firmware: {firmware})")

            # Log registration to CSV
            log_to_csv(device_id, firmware, 'connected')

            # Notify admin about new device
            notify_admin({
                'type': 'device_connected',
                'device_id': device_id,
                'firmware': firmware
            })

            # Send confirmation back to device
            response_topic = f"{MQTT_BASE_TOPIC}/{device_id}/response"
            client.publish(response_topic, json.dumps({
                'type': 'registered',
                'status': 'success'
            }))

        # Handle device data
        elif message_type == 'data':
            payload = data.get('payload', data)
            print(f"[MQTT] Data from {device_id}: {payload}")

            # Extract WiFi info
            ssid = payload.get('ssid', '')
            bssid = payload.get('bssid', '')
            rssi = payload.get('rssi', '')
            ip = payload.get('ip', '')
            firmware = connected_devices.get(device_id, {}).get('firmware', 'unknown')

            # Log WiFi status to CSV
            log_to_csv(device_id, firmware, 'wifi_status', ssid, bssid, rssi, ip)

            # Store last data for dashboard
            device_last_data[device_id] = payload

            # Update last seen
            if device_id in connected_devices:
                connected_devices[device_id]['last_seen'] = datetime.now()

            # Forward to admin
            notify_admin({
                'type': 'device_data',
                'device_id': device_id,
                'payload': payload
            })

            # Send acknowledgment
            response_topic = f"{MQTT_BASE_TOPIC}/{device_id}/response"
            client.publish(response_topic, json.dumps({
                'type': 'ack',
                'status': 'received'
            }))

        # Handle bird detection
        elif message_type == 'bird_detection':
            payload = data.get('payload', data)
            device_timestamp = payload.get('timestamp', 0)

            print(f"[MQTT] Bird detection from {device_id} at {device_timestamp}")

            # Log to birds CSV
            log_bird_detection(device_id, device_timestamp)

            # Update last seen
            if device_id in connected_devices:
                connected_devices[device_id]['last_seen'] = datetime.now()

            # Notify admin about bird detection
            notify_admin({
                'type': 'bird_detection',
                'device_id': device_id,
                'timestamp': device_timestamp
            })

            # Notify public API clients
            notify_public_detection(device_id, device_timestamp)

            # Send acknowledgment
            response_topic = f"{MQTT_BASE_TOPIC}/{device_id}/response"
            client.publish(response_topic, json.dumps({
                'type': 'ack',
                'status': 'received'
            }))

        # Handle OTA progress
        elif message_type == 'ota_progress':
            progress = data.get('progress', 0)
            message = data.get('message', '')
            print(f"[MQTT] [OTA] {device_id} progress: {progress}% - {message}")

            # Forward progress to admin
            notify_admin({
                'type': 'ota_progress',
                'device_id': device_id,
                'progress': progress,
                'message': message
            })

        # Handle status/heartbeat
        elif message_type == 'status':
            if device_id not in connected_devices:
                # Device already running, auto-register from status message
                firmware = data.get('firmware', 'unknown')
                connected_devices[device_id] = {
                    'firmware': firmware,
                    'last_seen': datetime.now()
                }
                print(f"[MQTT] Device auto-registered from status: {device_id} (firmware: {firmware})")
                notify_admin({
                    'type': 'device_connected',
                    'device_id': device_id,
                    'firmware': firmware
                })
            else:
                connected_devices[device_id]['last_seen'] = datetime.now()
            print(f"[MQTT] Status update from {device_id}")

    except json.JSONDecodeError:
        print(f"[MQTT] Invalid JSON from {msg.topic}: {msg.payload}")
    except Exception as e:
        print(f"[MQTT] Error processing message: {e}")

def on_disconnect(client, userdata, rc):
    """Callback when MQTT client disconnects"""
    if rc != 0:
        print(f"[MQTT] Unexpected disconnection. Reconnecting...")

def send_to_iot_device(device_id, payload):
    """Send message to specific IoT device via MQTT"""
    if device_id in connected_devices:
        topic = f"{MQTT_BASE_TOPIC}/{device_id}/command"
        mqtt_client.publish(topic, json.dumps({
            'type': 'command',
            'payload': payload
        }))
        print(f"[MQTT] Sent to device {device_id}: {payload}")
    else:
        print(f"[MQTT] Device {device_id} not connected")

def start_mqtt_client():
    """Start MQTT client"""
    global mqtt_client
    mqtt_client = mqtt.Client()
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.on_disconnect = on_disconnect

    # Set username and password
    mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
        print(f"[MQTT] Client started, connecting to {MQTT_BROKER}:{MQTT_PORT} as {MQTT_USERNAME}")
    except Exception as e:
        print(f"[MQTT] Failed to connect: {e}")

# Main entry point
if __name__ == '__main__':
    print("Starting servers...")

    # Start MQTT client
    start_mqtt_client()

    # Start public API server in separate thread
    def run_public_api():
        public_socketio.run(public_app, host='0.0.0.0', port=4120, debug=False, use_reloader=False)

    public_thread = threading.Thread(target=run_public_api, daemon=True)
    public_thread.start()
    print(f"[Public API] Started on port 4120")

    # Start Flask + SocketIO server (blocking)
    socketio.run(app, host='0.0.0.0', port=6235, debug=True, use_reloader=False)
