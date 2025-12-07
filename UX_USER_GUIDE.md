# QuickShare UX Improvements - User Guide

## New Features Overview

### 1. 📁 Native File Picker (Package & Send Cards)

#### How to Use:
1. **Browse for Folder** (Package Card):
   - Click on the "📁 Click to select or drag folder" area
   - A folder browser dialog opens
   - Select the folder you want to package
   - The folder name appears below the button
   - Click "Package to TAR.GZ" to proceed

2. **Browse for File** (Send Card):
   - Click on the "📄 Click to select or drag file" area
   - A file browser dialog opens
   - Select the tar.gz/tar/zip file to send
   - The filename appears below the button
   - Select a peer from the dropdown
   - Click "Send File" to proceed

#### Drag & Drop:
- **Faster method**: Simply drag your folder/file from Explorer/Finder directly onto the input area
- The area will highlight with a blue glow when you drag over it
- Drop to select
- Much faster than browsing dialogs!

#### What Changed:
- **Before**: Had to type file paths manually (e.g., "Portfolio" or "artifacts/portfolio.tar.gz")
- **After**: Native OS file picker with drag-and-drop support
- **Benefit**: No need to know absolute paths; visual feedback; faster selection

---

### 2. 📜 History Page Redesign

#### Access History:
- Click "History" link on home page
- View all previous transfers in modern card layout

#### Features:
1. **Transfer Cards** - Each card shows:
   - 📄 Filename (extracted from save path)
   - 📊 Total size (in MB/GB)
   - ⬆️ Bytes transferred
   - 📈 Progress percentage
   - ✓/✗/⧖ Status badge (Completed/Error/In Progress)
   - 🔐 SHA256 hash (first 16 chars)

2. **Search & Filter**:
   - Real-time search box at top
   - Type filename or transfer ID to filter
   - Results update instantly

3. **Actions** (per transfer):
   - **Extract**: Unpack the tarball to `received/` folder
   - **Verify**: Check SHA256 hash without extracting
   - **Copy Hash**: Copy full SHA256 to clipboard

4. **Clear History**:
   - Button to delete all transfer records
   - Confirmation popup to prevent accidents

#### What Changed:
- **Before**: Plain HTML table with raw data, hard to read
- **After**: Beautiful glassmorphic cards with status indicators
- **Benefit**: Better visual organization, easy filtering, one-click actions

---

### 3. 🔲 QR Code for Peer Discovery

#### How to Use (Receiver):
1. Start the receiver on your device:
   - Go to home page
   - Click "Start Receiver"
   - Wait for status to show "Running on port XXXXX"

2. **Share QR Code** with sender:
   - A QR code appears on the Receiver card
   - Show QR code to sender
   - Sender can scan with phone camera
   - Opens `quickshare://IP:PORT` to connect

3. **Manual Fallback**:
   - If QR scanner doesn't work, use "Copy" button
   - Pastes `IP:PORT` to clipboard
   - Sender can manually type into peer selector

#### How to Use (Sender):
1. Scan the receiver's QR code with your phone
2. The address is decoded and auto-fills the peer field
3. Select file and click "Send File"

#### What Changed:
- **Before**: Had to manually type IP address and port
- **After**: Scan QR code for instant peer discovery
- **Benefit**: Works on different LAN segments, no need to ask for IP address

---

## Accessibility Improvements

### Mobile Friendly:
- ✓ Responsive layouts - adapts to phone screens
- ✓ Touch-friendly buttons - larger tap targets
- ✓ Readable fonts - scales on small devices

### Keyboard Navigation:
- ✓ Tab through all buttons and inputs
- ✓ Space/Enter to activate buttons
- ✓ Native file picker works with keyboard

### Visual Clarity:
- ✓ Clear status indicators (green=done, red=error, blue=pending)
- ✓ Color contrast meets accessibility standards
- ✓ Toast notifications confirm all user actions

---

## Tips & Tricks

### File Picker:
- 💡 Drag-drop is faster than browsing dialogs
- 💡 You can drag multiple files (if webkitdirectory supports it)
- 💡 Button stays disabled until file selected - ensures you don't forget

### History:
- 💡 Search filters in real-time (no need to submit)
- 💡 Click "Copy Hash" to get full SHA256 for verification
- 💡 Extract directly from history card - no need to find files manually

### QR Code:
- 💡 QR code only shows when receiver is running
- 💡 Screenshot the QR code to share later
- 💡 Test with phone camera app if transfer app doesn't have scanner
- 💡 Use "Copy" button as fallback on same network

---

## Troubleshooting

### File Picker Not Opening?
- Check browser permissions for file access
- Clear browser cache and try again
- Try drag-drop method instead

### QR Code Not Scanning?
- Ensure receiver is actually running ("Running on port X" status shown)
- Try built-in phone camera app first
- Use "Copy" button to manually enter address

### History Not Showing Transfers?
- Ensure transfers have completed (refresh page)
- Check that you've actually sent/received files
- DB may not be initialized - check `received/` folder

---

## Best Practices

1. **Always verify hash** of important files (Verify button in history)
2. **Keep history** for audit trail of transfers
3. **Use QR code** for peer discovery on first connection
4. **Test drag-drop** early - much faster than browsing
5. **Screenshot QR code** if transferring between remote networks

---

## Feature Comparison

| Task | Before | After |
|------|--------|-------|
| Select folder | Type path | Click or drag |
| Select file | Type full path | Click or drag |
| Browse history | Plain table | Beautiful cards |
| Find transfer | Scroll table | Search box |
| Share with peer | Say IP:port | Scan QR code |
| Verify hash | Copy-paste hash | One click |

---

**Enjoy the improved QuickShare experience!** 🚀
