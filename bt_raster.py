import serial
import time
import struct
from collections import namedtuple
from enum import Enum
import pymupdf
from PIL import Image, ImageOps
import argparse
import tqdm

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

def compliment(number, bits):
    if number >= 0:
        return (number & (2**bits - 1))  # Ensure within bit range
    else:
        return ((number + 2**bits) & (2**bits - 1))


def compress_lines(raster, width, height):
    # I want this to work as a generator of compressed lines
    # this will change n1 and n2, as these describe the n-bytes sent
    out_lines = []
    width_bytes = width // 8
    COMP_AMT = 3 #how long it must be before we compress
    #main loop over a window of n len(line)
    for line in [raster[L*width_bytes:L*width_bytes+width_bytes] for L in range(height)]:
        #first pass: count each repeating sequence
        fp: list[tuple[char, int]] = []
        cursor = 0
        last = None
        n_count = 1

        while True:
            if cursor >= len(line):
                fp.append((last, n_count))
                break
            #initialize
            if last == None:
                last = line[cursor]
                #start = cursor

            elif last == line[cursor]:
                n_count += 1

            elif last != line[cursor]:
                fp.append((last, n_count))
                n_count = 1
                last = line[cursor]

            cursor += 1
        #print("First Pass:")
        #for i in fp:
        #    print(f"({hex(i[0])}, {i[1]})")

        #second pass: count each non repeating sequence
        sp: list[tuple[list[char],int]] = [] 
        cursor = 0
        n_count = 1
        scratch: list[char] = []

        while True:
            if cursor >= len(fp):
                if len(scratch) != 0:
                    sp.append((scratch, len(scratch)))
                scratch = []
                break

            current_char = fp[cursor][0]
            current_count = fp[cursor][1]

            if current_count >= COMP_AMT:
                if len(scratch) != 0:
                    sp.append((scratch, len(scratch)))
                    scratch = []
                
                sp.append(([current_char], current_count))
            
            elif current_count > 1 and current_count < COMP_AMT:
                scratch.extend([current_char for i in range(current_count)])

            else: # current_count == 1
                scratch.append(current_char)

            cursor += 1

        #print("Second Pass:")
        #for i in sp:
        #    print(f"({[hex(c) for c in i[0]]}, {hex(i[1])}, {i[1]}))")
        
        #third pass, build bytearray

        comp_line = bytearray()

        for val in sp:
            bts = val[0]
            blen = val[1]

            if len(bts) == 1: #repeating section
                repeat_count = compliment(-(blen), 8)
                comp_line.append(repeat_count)
                comp_line.append(bts[0])

            else:
                comp_line.append(blen-1)
                comp_line.extend(bts)

        #print("Final Pass:")
        compressed = ''.join([f'{byte:02x}' for byte in comp_line])
        original = ''.join([f'{byte:02x}' for byte in line])
        #print(f"Original(len:{hex(len(line))}): {original} \nCompressed(len:{len(comp_line)}): {compressed}")
        #print("\n###########\n")
        out_lines.append(comp_line)
        
    return out_lines


def uncompressed_lines(raster, width, height):
    width_bytes = width // 8
    lines = []
    for line in [raster[L*width_bytes:L*width_bytes+width_bytes] for L in range(height)]:
        flipped_line = line# bytes([to_compliment(b, 8) for b in line])
        uncompressed_line = b'\x32' + flipped_line[0:0x32+1] + b'\x32' + flipped_line[0x33:]
        lines.append(uncompressed_line)

    return lines



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
    return img_bytes

def image_raster_format(img, width, height):
    width_bytes = width // 8
    dpi = 360
    w,h = img.size
    
    width_scale = 0
    height_scale = 0

    if w > width:
        width_scale = width / w 
    else:
        width_scale = w / width
    width_scale = width / w 
    new_width = w * width_scale
    new_height = h * width_scale
    
    if new_height > height:
        height_scale = 0
        if h > height:
            height_scale = height / h 
        else:
            height_scale = h / height

        new_height = h * height_scale * width_scale

    print(f"old width: {w}, old height: {h}")
    print(f"new width: {int(new_width)}, new height: {int(new_height)}")
    img = img.convert("L")
    img = img.resize((int(new_width), int(new_height)), Image.LANCZOS)
    
    #will fit but it's not centered
    background = Image.new('L', (816, 1180), color=255)
    space = (height - new_height) / 2

    background.paste(img, (0, int(space)))
    #background.show()
    img = background

    img = img.convert("1")
    img = ImageOps.mirror(img)
    img = ImageOps.invert(img)
    #img.show()
    img_bytes = img.tobytes()
    #we want img[L*width_bytes:L*width_bytes+width_bytes]
    return img_bytes

def print_image_raster(port: str, baudrate: int, img_raster, timeout: float = 5):
    try:
        with serial.Serial(port, baudrate, timeout=timeout) as ser:
            print(f"Opened serial port {port} at {baudrate} baud")
            
            ser.write(get_info)
            #print(f"Sent: {get_info}")
            response = ser.read(32)
            #parse_status(response)

            ser.write(init_command)
            ser.write(switch_command_mode_raster)

            ser.write(set_exp_mode_no_buf_clear)
            ser.write(set_tiff_compression)
            ser.write(clear_raster_lines)

            #raster = raster_format(page, 816, 1180)
            lines = uncompressed_lines(img_raster, 816, 1180)
            #lines = compress_lines(raster, 816, 1180)

            for line in tqdm.tqdm(lines):
                rc = raster_command(len(line),0, line)
                #print(''.join([f'{byte:02x}' for byte in rc]))
                ser.write(rc)

            ser.write(pagefeed)

            time.sleep(1)

            response = ser.read(32)
            #print(response)
            #parse_status(response)

    except serial.SerialException as e:
        print(f"Serial error: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")


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

            raster = raster_format(page, 816, 1180)
            lines = uncompressed_lines(raster, 816, 1180)
            #lines = compress_lines(raster, 816, 1180)

            for line in lines:
                rc = raster_command(len(line),0, line)
                print(''.join([f'{byte:02x}' for byte in rc]))
                ser.write(rc)

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
    parser.add_argument("--compress", "-c", action="store_true", help="experimental tiff pack")
    parser.add_argument("--test", "-t", action="store_true", help="only process, don't print")
    args = parser.parse_args()
    serial_port = args.serialport
    baud_rate = args.baud

    if ".pdf" in args.file:
        doc = pymupdf.open(args.file)
        if args.page is not None and args.page in range(len(doc)):
            if not args.test:
                print_page_raster(serial_port, baud_rate, doc[args.page-1], timeout = 20)
            else:
                raster = raster_format(doc[args.page-1], 816, 1180)
                for line in compress_lines(raster, 816, 1180):
                    print(''.join([f'{byte:02x}' for byte in raster_command(len(line), 0, line)]))

                print(hex(compliment(-1, 8)))
                    #print(line)

        elif args.range is not None:
            start, end = args.range
            for idx, page in enumerate(doc[int(start, 10):int(end,10)+1]):
                print(f"Printing page: {idx}")
                if not args.test:
                    print_page_raster(serial_port, baud_rate, page, timeout = 20)
                else:
                    lines = raster_format(page, 816, 1180)
                    print(len(lines))
                    for line in lines:
                        print(compress_line(line))

    else:
        image = Image.open(args.file)
        ir = image_raster_format(image, 816, 1180)
        print(f"Printing {args.file.split('/')[-1]}")
        if not args.test:
            print_image_raster(serial_port, baud_rate, ir, timeout=20)




