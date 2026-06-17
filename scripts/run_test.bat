@echo off
cd /d D:\api\quantpy-stock-analysis
echo Testing qstock...
python get_stocks_improved.py > test_output.txt 2>&1
echo Output saved to test_output.txt
type test_output.txt
pause
