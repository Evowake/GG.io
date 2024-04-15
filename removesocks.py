# Open the file for reading
with open('proxy_list.txt', 'r') as file:
    lines = file.readlines()

# Remove the 'socks5://' prefix from each line
modified_lines = [line.replace('socks5://', '') for line in lines]

# Open the file for writing and write the modified lines
with open('proxy_list.txt', 'w') as file:
    file.writelines(modified_lines)
