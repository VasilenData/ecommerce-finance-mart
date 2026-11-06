import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mart.pipeline import build  # noqa: E402

HEADER = "Invoice,StockCode,Description,Quantity,InvoiceDate,Price,Customer ID,Country"
ROWS = [
    "500001,10001,  Red   Mug ,2,2011-01-05 10:00:00,3.00,1001,United Kingdom",
    "500001,10002a,Blue Mug,1,2011-01-05 10:00:00,5.00,1001,United Kingdom",
    "500001,10001,  Red   Mug ,2,2011-01-05 10:00:00,3.00,1001,United Kingdom",
    "500002,10001,Red Mug,5,2011-02-10 09:00:00,3.00,1002,EIRE",
    "C500003,10001,Red Mug,-1,2011-02-12 11:00:00,3.00,1002,EIRE",
    "500004,POST,POSTAGE,1,2011-02-10 09:00:00,10.00,1002,EIRE",
    "500005,10003,Green Mug,3,2011-03-01 12:00:00,2.00,,United Kingdom",
    "500006,10003,lost,-5,2011-03-02 12:00:00,0,,United Kingdom",
    "500007,TEST001,This is a test product.,1,2011-03-03 08:00:00,1.00,,United Kingdom",
    "A500008,B,Adjust bad debt,1,2011-03-04 08:00:00,-100.00,,United Kingdom",
    "C500009,D,Discount,-1,2011-03-05 08:00:00,4.00,1001,United Kingdom",
    "500010,10004,Zero Qty,0,2011-03-06 08:00:00,1.00,1001,United Kingdom",
    "C500011,10001,Red Mug,2,2011-03-07 08:00:00,3.00,1001,United Kingdom",
    "500012,10001,Red Mug,x,2011-03-08 08:00:00,3.00,1001,United Kingdom",
    "500013,10001,Red Mug,1,2011-03-09 08:00:00,3.00,1001,United Kingdom",
    "500014,M,Manual,-1,2011-03-09 09:00:00,0,,United Kingdom",
    "C500015,AMAZONFEE,AMAZON FEE,-1,2011-03-10 09:00:00,20.00,,United Kingdom",
    "500016,10005,Widget,10,2011-03-10 10:00:00,2.00,1003,United Kingdom",
    "C500017,10005,Widget,-10,2011-03-10 10:30:00,2.00,1003,United Kingdom",
    "500018,10006,Gadget,4,2011-03-10 10:00:00,2.00,1003,United Kingdom",
    "C500019,10006,Gadget,-4,2011-03-12 10:00:00,2.00,1003,United Kingdom",
]


@pytest.fixture(scope="session")
def con(tmp_path_factory):
    path = tmp_path_factory.mktemp("fx") / "raw.csv"
    path.write_text(HEADER + "\n" + "\n".join(ROWS) + "\n")
    return build(path)
