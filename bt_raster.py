import serial
import time
import struct
from collections import namedtuple
from enum import Enum
import pymupdf
from PIL import Image, ImageOps
import argparse

status = namedtuple("status", "Head_Mark Size Brother_Code Series Model Country info NA Error_1 Error_2 \
        Media_Width Media_Kind n_colors font Nippon_font Mode Density Media_Length \
        Status_Kind Phase_Kind Phase_number_upper Phase_number_lower notice_number n_bytes_expansion hw_setting")


class StatusKind(Enum):
    REPLY_TO_STATUS_REQUEST = 0
    PRINT_COMPLETE = 1
    ERROR_OCCURED = 2
    NOT_USED1 = 3
    NOT_USED2 = 4
    NOTICE = 5
    PHASE_CHANGE =6

def parse_status(response):
    up = status._make(struct.unpack(">BBBBBBBBBBBBBBBBBBBBBBBBq", response))
    for name in status._fields:
        print(f"{name}: {hex(getattr(up, name))}")


get_info = bytearray([0x1b, 0x69, 0x53])
init_command = bytearray([0x1b, ord('@')])
switch_command_mode_raster = bytearray([0x1b, ord('i'), ord('a'), 0x1])
set_exp_mode_no_buf_clear = bytearray([0x1b, 0x69, 0x4b, 0x80])

raster_command = lambda n1, n2, data : bytearray([ord('G'), n1, n2]) + bytes(data)
set_tiff_compression = bytearray([0x4d, 0x02])
clear_raster_lines = bytearray([0x5a for i in range(6)])

pagefeed = bytearray([0x0C])


def to_compliment(val, nbits):
    return val + (1<<nbits) % (1 << nbits)

def raster_format(page, width, height):
    width_bytes = width // 8
    dpi = 360
    scale = dpi / 72
    # Scale factor: PDFs default to 72 DPI, so we adjust for 360 DPI
    matrix = pymupdf.Matrix(scale, scale)
    # Render page to a rasterized image
    pix = page.get_pixmap(matrix=matrix)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    img = img.convert("L")
    img = img.resize((width, height), Image.LANCZOS)
    img = img.convert("1")
    img = ImageOps.mirror(img)
    img = ImageOps.invert(img)
    #img.show()
    img_bytes = img.tobytes()
    #we want img[L*width_bytes:L*width_bytes+width_bytes]
    lines = []
    for line in [img_bytes[L*width_bytes:L*width_bytes+width_bytes] for L in range(height)]:
        flipped_line = bytes([to_compliment(b, 8) for b in line])
        uncompressed_line = b'\x32' + flipped_line[0:0x32+1] + b'\x32' + flipped_line[0x33:]
        lines.append(uncompressed_line)
        #print(f"Len: {hex(len(uncompressed_line))}")
        #print(uncompressed_line)

    return lines

def print_page_raster(port: str, baudrate: int, page, timeout: float = 5):

    try:
        with serial.Serial(port, baudrate, timeout=timeout) as ser:
            print(f"Opened serial port {port} at {baudrate} baud")
            
            ser.write(get_info)
            print(f"Sent: {get_info}")
            response = ser.read(32)
            parse_status(response)

            ser.write(init_command)
            ser.write(switch_command_mode_raster)

            ser.write(set_exp_mode_no_buf_clear)
            ser.write(set_tiff_compression)
            ser.write(clear_raster_lines)

            lines = raster_format(page, 816, 1180)

            for line in lines:
                ser.write(raster_command(0x68,0, line))

            ser.write(pagefeed)

            time.sleep(1)

            response = ser.read(32)
            print(response)
            parse_status(response)

    except serial.SerialException as e:
        print(f"Serial error: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serialport", "-s", default="/dev/rfcomm0", help="rfcomm port, default /dev/rfcomm0")
    parser.add_argument("--baud", "-b", default=115200, help="baud rate, default 115200")
    parser.add_argument("--file", "-f", help="pdf file path")
    parser.add_argument("--page", "-p", type=int, help="page of pdf to print")
    parser.add_argument("--range", "-r", nargs=2, help="range of pages to print")
    parser.add_argument("--test", "-t", action="store_true", help="only process, don't print")
    args = parser.parse_args()
    serial_port = args.serialport
    baud_rate = args.baud
    
    doc = pymupdf.open(args.file)
    if args.page is not None and args.page in range(len(doc)):
        if not args.test:
            print_page_raster(serial_port, baud_rate, doc[args.page-1], timeout = 20)
        else:
            lines = raster_format(doc[args.page-1], 816, 1180)

    elif args.range is not None:
        start, end = args.range
        for idx, page in enumerate(doc[int(start, 10):int(end,10)+1]):
            print(f"Printing page: {idx}")
            if not args.test:
                print_page_raster(serial_port, baud_rate, page, timeout = 20)
            else:
                lines = raster_format(page, 816, 1180)
