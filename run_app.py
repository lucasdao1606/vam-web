import os
import sys
import streamlit.web.cli as stcli

def resolve_path(path):
    """Lấy đường dẫn tuyệt đối tới file (hỗ trợ cả khi đã đóng gói thành .exe)"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, path)
    return os.path.join(os.path.abspath("."), path)

if __name__ == "__main__":
    # Đảm bảo trỏ đúng tới file app.py chính của bạn
    app_path = resolve_path("app.py")
    
    # Thiết lập tham số dòng lệnh tương đương: streamlit run app.py
    sys.argv = [
        "streamlit",
        "run",
        app_path,
        "--global.developmentMode=false"
    ]
    
    # Chạy ứng dụng Streamlit
    sys.exit(stcli.main())