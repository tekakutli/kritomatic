# workbench/workbench_html.py
from wb_base import WB_CSS, WB_BODY
from wb_client import WB_CLIENT_JS

def render():
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n<title>Workbench</title>\n"
        "<style>\n" + WB_CSS + "\n</style>\n"
        "</head>\n<body>\n" + WB_BODY +
        "<script>\n\"use strict\";\n" + WB_CLIENT_JS + "\n</script>\n"
        "</body>\n</html>\n"
    )