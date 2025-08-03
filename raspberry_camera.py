"""
라즈베리파이 카메라 스트리밍
Flask 서버로 실시간 카메라 영상을 전송
"""
import cv2
import numpy as np
import requests
import time
import base64
import argparse
import logging
from picamera2 import Picamera2

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('RaspiCamera')

def main():
    # 명령행 인수 파싱
    parser = argparse.ArgumentParser(description='라즈베리파이 카메라 스트리밍 클라이언트')
    parser.add_argument('--server', type=str, default='http://192.168.219.100:5000',
                        help='Flask 서버 URL (예: http://your-aws-server.com:5000)')
    parser.add_argument('--width', type=int, default=640,
                        help='카메라 캡처 너비 (기본값: 640)')
    parser.add_argument('--height', type=int, default=480,
                        help='카메라 캡처 높이 (기본값: 480)')
    parser.add_argument('--fps', type=int, default=12,
                        help='목표 FPS (기본값: 12)')
    parser.add_argument('--quality', type=int, default=70,
                        help='JPEG 품질 (1-100, 기본값: 70)')
    parser.add_argument('--camera-id', type=str, default='camera_0',
                        help='카메라 ID (기본값: camera_0)')
    args = parser.parse_args()
    
    # 서버 URL 설정
    server_url = args.server.rstrip('/')
    stream_url = f"{server_url}/receive_camera_frame"
    status_url = f"{server_url}/api/status"
    
    logger.info(f"서버 URL: {stream_url}")
    logger.info(f"설정: {args.width}x{args.height}, {args.fps}fps, 품질={args.quality}")
    logger.info(f"카메라 ID: {args.camera_id}")
    
    # 카메라 ID
    camera_id = args.camera_id
    
    try:
        # PiCamera2 초기화
        picam = Picamera2()
        picam.configure(picam.create_video_configuration(main={"size": (args.width, args.height)}))
        picam.set_controls({"FrameDurationLimits": (33333, 33333)})  # 약 30fps
        picam.start()
        
        logger.info("카메라 초기화 완료")
        time.sleep(2)  # 카메라 안정화 대기
        
        # 프레임 간격 계산 (초 단위)
        frame_interval = 1.0 / args.fps
        
        # 스트리밍 루프
        logger.info("영상 스트리밍 시작")
        last_frame_time = time.time()
        last_status_check = time.time()
        frame_count = 0
        is_active = False
        status_check_interval = 1.0  # 1초마다 서버 상태 확인
        
        while True:
            current_time = time.time()
            
            # 서버 상태 확인 (주기적으로)
            if current_time - last_status_check >= status_check_interval:
                try:
                    response = requests.get(status_url, timeout=1.0)
                    if response.status_code == 200:
                        status_data = response.json()
                        if 'current_source' in status_data:
                            # 이 카메라가 현재 활성화되어 있는지 확인
                            was_active = is_active
                            is_active = status_data['current_source'] == camera_id
                            
                            # 활성화 상태가 변경되면 로그 출력
                            if is_active != was_active:
                                if is_active:
                                    logger.info("이 카메라가 활성화되었습니다. 프레임 전송을 시작합니다.")
                                else:
                                    logger.info("이 카메라가 비활성화되었습니다. 프레임 전송을 중단합니다.")
                    
                    last_status_check = current_time
                    
                    # 비활성 상태일 때는 더 자주 확인
                    status_check_interval = 0.5 if not is_active else 3.0
                    
                except Exception as e:
                    logger.error(f"서버 상태 확인 오류: {str(e)}")
                    last_status_check = current_time
            
            # 이 카메라가 활성화되지 않았으면 프레임 전송 건너뛰기
            if not is_active:
                time.sleep(0.1)  # 서버가 활성화 상태를 변경할 때까지 대기
                continue
                
            elapsed = current_time - last_frame_time
            
            # FPS 조절 (목표 FPS 유지)
            if elapsed < frame_interval:
                time.sleep(0.001)  # CPU 사용량 감소
                continue
                
            # 프레임 캡처
            frame = picam.capture_array()
            
            # RGB를 BGR로 변환 (OpenCV 표준)
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            
            # JPEG으로 인코딩
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), args.quality]
            _, buffer = cv2.imencode('.jpg', frame, encode_param)
            
            # Base64로 인코딩
            frame_base64 = base64.b64encode(buffer).decode('utf-8')
            
            try:
                # 서버로 전송 (카메라 ID 포함)
                response = requests.post(
                    stream_url, 
                    json={
                        'camera_id': camera_id,
                        'frame': frame_base64
                    },
                    timeout=1.0
                )
                
                if response.status_code == 200:
                    resp_data = response.json()
                    frame_count += 1
                    
                    # 이 카메라가 비활성화되면 활성화 상태 업데이트
                    if 'is_active' in resp_data and not resp_data['is_active']:
                        is_active = False
                        logger.info("서버에서 이 카메라가 비활성화되었음을 알려왔습니다. 프레임 전송을 중단합니다.")
                        
                    if frame_count % 30 == 0:  # 30프레임마다 로그 출력
                        logger.info(f"프레임 전송 성공: {frame_count}번째 프레임, FPS={1.0/elapsed:.2f}")
                else:
                    logger.warning(f"프레임 전송 실패: 상태 코드 {response.status_code}")
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"서버 연결 오류: {str(e)}")
                time.sleep(1.0)  # 오류 발생 시 재시도 간격
            
            # 마지막 프레임 시간 업데이트
            last_frame_time = current_time
            
    except KeyboardInterrupt:
        logger.info("사용자에 의해 중단됨")
    except Exception as e:
        logger.error(f"오류 발생: {str(e)}")
    finally:
        if 'picam' in locals():
            picam.stop()
        logger.info("프로그램 종료")

if __name__ == "__main__":
    main()
