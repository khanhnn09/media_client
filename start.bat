@echo off
setlocal enabledelayedexpansion
title Selenium Manager
cd /d "%~dp0"

rem ============================================================================
rem start.bat — 1-click setup + run cho client_tool (2026-07-17)
rem
rem Truoc day script nay CAN venv o repo root (..\venv, tao boi setup_venv.bat
rem cua ToolSub) - client_tool khong the copy rieng sang may khac ma chay duoc.
rem Gio venv la LOCAL, nam ngay trong client_tool\venv - script tu tao venv +
rem tu cai requirements.txt neu chua co, nen chi can copy nguyen thu muc
rem client_tool\ sang may moi roi bam file nay la du (mien Python + Chrome da
rem cai san tren may do - 2 thu nay KHONG the tu dong cai qua .bat don gian).
rem ============================================================================

set VENV=venv
set PYEXE=%VENV%\Scripts\python.exe

rem ── Buoc 0: tu cap nhat code moi nhat qua git pull (2026-07-21) ──
rem client_tool gio la 1 git repo rieng (remote machinemedia.git) - moi may
rem worker tu dong nhan ban moi nhat MOI LAN mo app, khong can nho chay tay.
rem Dung --ff-only (fast-forward CHI, khong tu merge/rebase): neu may nay co
rem commit cuc bo rieng hoac lich su da re nhanh, lenh that bai SACH (khong
rem dung gi toi working tree) thay vi tu y merge/ghi de - an toan cho viec
rem chay TU DONG khong nguoi giam sat tren nhieu may. Loi mang/khong phai git
rem repo chi log canh bao roi van chay tiep voi code hien co, khong chan app.
if exist ".git" (
    echo [INFO] Dang kiem tra ban cap nhat...
    git pull --ff-only
    if errorlevel 1 (
        echo [WARN] Khong tu cap nhat duoc ^(mat mang, hoac co thay doi cuc bo tren may nay^) - tiep tuc chay voi code hien co.
    )
) else (
    echo [INFO] Chua phai git repo o thu muc nay - bo qua buoc tu cap nhat.
)

rem ── Buoc 1: tim Python interpreter tren may (khong co san thi bao loi ro rang) ──
if not exist "%PYEXE%" (
    where py >nul 2>nul
    if !errorlevel! equ 0 (
        set PYLAUNCH=py -3
    ) else (
        where python >nul 2>nul
        if !errorlevel! equ 0 (
            set PYLAUNCH=python
        ) else (
            echo.
            echo [ERROR] Khong tim thay Python tren may nay.
            echo         Cai Python 3.10+ tu https://www.python.org/downloads/
            echo         ^(nho tick "Add python.exe to PATH" luc cai^) roi chay lai file nay.
            echo.
            pause
            exit /b 1
        )
    )

    echo [INFO] Chua co venv - dang tao moi tai .\%VENV% ...
    !PYLAUNCH! -m venv %VENV%
    if not exist "%PYEXE%" (
        echo [ERROR] Tao venv that bai. Kiem tra Python cai dat co dung khong.
        pause
        exit /b 1
    )
)

rem ── Buoc 2: cai/cap nhat cac thu vien can thiet (idempotent, chay lai khong sao) ──
rem --no-cache-dir (2026-07-18): pip mac dinh dung wheel cache dung chung o
rem %LOCALAPPDATA%\pip\cache (o C:, KHONG lien quan gi toi venv dang o dau -
rem day la hanh vi binh thuong cua pip tren Windows). Cache nay CO THE bi khoa/
rem hong boi tien trinh khac (antivirus dang quet, 1 lan chay start.bat truoc
rem bi ngat giua chung...) - gap thuc te: "PermissionError ... pip\cache\wheels\
rem ...\undetected_chromedriver-....whl". --no-cache-dir bo qua hoan toan cache
rem dung chung do, tai thang truc tiep tu PyPI moi lan - cham hon 1 chut nhung
rem khong bao gio bi chan boi cache hong/bi khoa tren may la.
echo [INFO] Kiem tra thu vien...
"%PYEXE%" -m pip install --upgrade pip --quiet --no-cache-dir
"%PYEXE%" -m pip install -r requirements.txt --quiet --no-cache-dir
if errorlevel 1 (
    echo.
    echo [ERROR] Cai thu vien that bai - xem log phia tren.
    pause
    exit /b 1
)

rem ── Buoc 3: bootstrap .env neu chua co (copy tu .env.example) ──
if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo [INFO] Da tao .env tu .env.example - MO FILE .env VA SUA FLOW_API_URL
        echo        cho dung ^(URL backend chinh^) truoc khi dung that.
    )
)

rem ── Buoc 4: chay app ──
echo [INFO] Khoi dong Selenium Manager...
"%PYEXE%" main.py

pause
