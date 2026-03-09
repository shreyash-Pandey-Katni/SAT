# Complete CNL Command List for Web-Based Testing

## Overview

This document provides a comprehensive list of all CNL commands available in the SAT/ALF tool for web-based test automation. Each command is extracted from the actual source code implementation.

---

## Command Categories

### 1. Browser Management Commands

#### StartWebBrowser

Launches a web browser and navigates to a URL.

```cnl
StartWebBrowser "http://localhost:8080/app";
```

#### GoToURL

Navigates to a URL in the current browser without creating a new instance.

```cnl
GoToURL "http://example.com/page";
```

#### CloseWebBrowser

Closes the current browser window.

```cnl
CloseWebBrowser;
```

#### CloseAllWebBrowsers

Closes all open browser windows.

```cnl
CloseAllWebBrowsers;
```

#### RefreshBrowser

Refreshes the current page.

```cnl
RefreshBrowser;
```

#### GoBackInBrowser

Navigates to the previous page.

```cnl
GoBackInBrowser;
```

#### SwitchToWindow

Switches to a specific browser window by title.

```cnl
SwitchTo "WindowTitle" Window;
```

#### SwitchToPreviousWindow

Switches back to the previous window.

```cnl
SwitchToPreviousWindow;
```

#### WaitForPageToLoad

Waits for the page to finish loading.

```cnl
WaitForPageToLoad;
```

---

### 2. Click Commands

#### Click

Standard click on an element.

```cnl
Click "ElementId" ControlType;
Click ControlType following "RelativeElement" ControlType;
Click "ElementId" ControlType following "RelativeElement" ControlType;
```

#### ClickJS

JavaScript-based click (for hidden/overlapping elements).

```cnl
ClickJS "ElementId" Button;
```

#### ClickNative

Native OS-level click (simulates real user).

```cnl
ClickNative "ElementId" Button;
ClickNative Link following "Label" Label;
```

#### DoubleClick

Double-click on an element.

```cnl
DoubleClick "ElementId" Button;
DoubleClick Link following "Label" Label;
DoubleClick "Option" option in "ComboBox" ComboBox;
```

#### RightClick

Right-click (context menu).

```cnl
RightClick "ElementId" Link;
RightClick Link following "Label" Label;
```

#### ClickAndHold

Clicks and holds on an element.

```cnl
ClickAndHold "ElementId" Button;
```

---

### 3. Input Commands

#### Type

Types text into an input field.

```cnl
Type "Text" in "FieldLabel" TextField;
Type "Text" in TextField following "Label" Label;
Type "Text" in TextArea following "Label" Label;
```

#### TypeNative

Types using native keyboard events (simulates real typing).

```cnl
TypeNative "Text" in "FieldLabel" TextField;
TypeNative "Text" in TextField following "Label" Label;
```

#### ClickAndType

Clicks on a field and then types.

```cnl
ClickAndType "Text" in "FieldLabel" TextField;
ClickAndType "Text" in TextField following "Label" Label;
```

#### Clear

Clears the content of an input field.

```cnl
Clear "FieldLabel" TextField;
Clear TextField following "Label" Label;
```

#### PressKey

Simulates pressing a keyboard key.

```cnl
PressKey "ENTER" in "Element" TextField;
PressKey "TAB" in "Element" TextField;
```

**Supported Keys:** ENTER, TAB, ESCAPE, DELETE, BACKSPACE, ARROW_UP, ARROW_DOWN, ARROW_LEFT, ARROW_RIGHT, etc.

#### Paste

Pastes content into a field.

```cnl
Paste "Text" using keyboard in "FieldName" TextField;
Paste "Text" using mouse in TextField following "Label" Label;
```

---

### 4. Selection Commands

#### Select

Selects an option from a dropdown/combobox.

```cnl
Select "OptionValue" in "ComboBoxLabel";
Select "OptionValue" in "ComboBoxLabel" ComboBox;
Select "OptionValue" in ComboBox following "Label" Label;
```

#### MultiSelect

Selects multiple options (for multi-select dropdowns).

```cnl
MultiSelect "Option1#Option2" in "ComboBoxLabel";
MultiSelect "Option1#Option2" in "ComboBoxLabel" ComboBox;
MultiSelect "Option1#Option2" in ComboBox following "Label" Label;
```

**Note:** Use `#` to separate multiple options.

#### UnSelect

Unselects options from a multi-select dropdown.

```cnl
UnSelect "Option1#Option2" in "ComboBoxLabel" ComboBox;
UnSelect "Option1" in ComboBox following "Label" Label;
```

#### Check / Uncheck

Checks or unchecks a checkbox or radio button.

```cnl
Check "ElementName" CheckBox;
Uncheck "ElementName" CheckBox;
Check CheckBox following "Label" Label;
Check "No" RadioButton following "Enabled" Label;
Uncheck RadioButton preceding "Label" Label;
```

---

### 5. Mouse Actions

#### MouseOver / HoverOn

Hovers the mouse over an element.

```cnl
MouseOver "ElementId" Label;
MouseOver Link following "Label" Label;
MouseOver "ElementId" Image below "Label" Label;
```

#### DragAndDrop

Drags an element and drops it on another.

```cnl
DragAndDrop "SourceElement" ControlType to "TargetElement" ControlType;
```

#### ScrollAndFindElement

Scrolls to find and interact with an element.

```cnl
ScrollAndFindElement "ElementId" Button;
```

#### ScrollInToView

Scrolls an element into view.

```cnl
ScrollInToView "ElementId" Button;
```

---

### 6. Verification Commands

#### Verify

Verifies element properties or text content.

```cnl
Verify text of "Element" ControlType;
Verify "AttributeName" of "Element" ControlType;
Verify text of ControlType following "Label" Label;
```

#### VerifyBrowserInstanceCount

Verifies the number of open browser instances.

```cnl
VerifyBrowserInstanceCount "2";
```

#### VerifyContentsInFile

Verifies contents in a file.

```cnl
VerifyContentsInFile "FilePath" contains "ExpectedText";
```

#### VerifyHTTPResponse

Verifies HTTP response status.

```cnl
VerifyHTTPResponse "URL" returns "200";
```

---

### 7. Alert Handling

#### AcceptAllAlert

Accepts all alert dialogs.

```cnl
AcceptAllAlert;
```

#### RejectAlert

Rejects/dismisses an alert dialog.

```cnl
RejectAlert;
```

#### EnterTextAndAcceptAlert

Enters text in a prompt and accepts it.

```cnl
EnterTextAndAcceptAlert "TextToEnter";
```

---

### 8. File Operations

#### AttachFile

Attaches a file to a file input element.

```cnl
AttachFile "C:\\path\\to\\file.txt" to "ButtonName" Button;
AttachFile "C:\\path\\to\\file.txt" to Button following "Label" Label;
```

#### AttachFileNative

Attaches a file using native file dialog.

```cnl
AttachFileNative "C:\\path\\to\\file.txt" to "ButtonName" Button;
AttachFileNative "C:\\path\\to\\file.txt" to Button following "Label" Label;
```

#### DeleteDownloadedFiles

Deletes downloaded files from the browser's download directory.

```cnl
DeleteDownloadedFiles;
```

#### DeleteFiles

Deletes specified files.

```cnl
DeleteFiles "C:\\path\\to\\file.txt";
```

#### DownloadFile

Downloads a file from a URL.

```cnl
DownloadFile "URL" to "LocalPath";
```

---

### 9. Screenshot Commands

#### CaptureSnapshot

Captures a screenshot of an element or area.

```cnl
CaptureSnapshot "C:\\image.png" for "Element" ControlType;
CaptureSnapshot "C:\\image.png" for "Element" ControlType preceding "RelativeElement" ControlType;
CaptureSnapshot "C:\\image.png" for Label preceding "Element" Div;
```

#### CompareImages

Compares two images for differences.

```cnl
CompareImages "Image1Path" with "Image2Path";
```

---

### 10. Media Commands

#### PlayPauseVideo

Plays or pauses a video element.

```cnl
PlayPauseVideo "VideoName" Video;
PlayPauseVideo Video following "Label" Link;
PlayPauseVideo "VideoName" Video following "Label" Link;
```

---

### 11. Form Submission

#### Submit

Submits a form by pressing Enter in a field.

```cnl
Submit "FieldLabel" TextField;
Submit "FieldId" TextField below "Label" Label;
Submit "Text" in TextField following "Label" Label;
```

#### SubmitNative

Submits using native keyboard events.

```cnl
SubmitNative "FieldLabel" TextField;
SubmitNative "FieldId" TextField below "Label" Label;
SubmitNative "Text" in TextField following "Label" Label;
```

---

### 12. Data Storage

#### StoreAttribute

Stores element attributes or text for later use.

```cnl
Store text of "Element" ControlType;
Store "AttributeName" of "Element" ControlType;
Store text of ControlType preceding "Element" ControlType;
Store text of "Element" ControlType following "RelativeElement" ControlType;
```

---

### 13. Control Flow Commands

#### IfSatisfy

Conditional execution based on element state or value.

```cnl
IfSatisfy "Condition" then
    // Commands to execute if true
EndIf;
```

#### WhileLoop

Executes commands in a loop while a condition is true.

```cnl
WhileLoop "Condition" do
    // Commands to execute
EndWhile;
```

#### ForEach

Iterates over a collection of elements.

```cnl
ForEach "Element" in "Collection" do
    // Commands to execute
EndForEach;
```

#### Sleep

Pauses execution for a specified duration.

```cnl
Sleep "5" seconds;
Sleep "500" milliseconds;
```

#### FailTestCase

Explicitly fails the test case.

```cnl
FailTestCase "Reason for failure";
```

#### PassTestCase

Explicitly passes the test case.

```cnl
PassTestCase "Reason for passing";
```

---

### 14. Property Management

#### SetProperty

Sets a property value for use in subsequent commands.

```cnl
SetProperty "PropertyName" to "Value";
```

#### LoadProperties

Loads properties from a file.

```cnl
LoadProperties "FilePath";
```

---

### 15. Cookie Management

#### ClearAllCookies

Clears all browser cookies.

```cnl
ClearAllCookies;
```

---

### 16. Console Management

#### ClearErrorsInConsole

Clears JavaScript errors in the browser console.

```cnl
ClearErrorsInConsole;
```

---

### 17. REST API Commands

#### PerformRest

Performs REST API calls.

```cnl
PerformRest "GET" on "URL";
PerformRest "POST" on "URL" with "RequestBody";
```

---

### 18. File Comparison

#### CompareFiles

Compares two files for differences.

```cnl
CompareFiles "File1Path" with "File2Path";
```

#### CompareJSON

Compares two JSON files or strings.

```cnl
CompareJSON "JSON1" with "JSON2";
```

---

### 19. SVG Commands

#### SwitchToSVG

Switches context to an SVG element.

```cnl
SwitchToSVG "SVGElementId";
```

#### ResizeSVGObject

Resizes an SVG object.

```cnl
ResizeSVGObject "SVGElementId" to "Width" "Height";
```

#### ConnectSvgObject

Connects SVG objects (for diagram tools).

```cnl
ConnectSvgObject "SourceId" to "TargetId";
```

---

### 20. Utility Commands

#### CallJavaMethod

Calls a custom Java method.

```cnl
CallJavaMethod "ClassName.MethodName" with "Parameters";
```

#### ExecuteProgram

Executes an external program.

```cnl
ExecuteProgram "ProgramPath" with "Arguments";
```

#### ExtractValueFromMessage

Extracts a value from a message using regex.

```cnl
ExtractValueFromMessage "Pattern" from "Message";
```

#### WriteLog

Writes a message to the log.

```cnl
WriteLog "Message to log";
```

#### WriteToFile

Writes content to a file.

```cnl
WriteToFile "Content" to "FilePath";
```

#### StartCNLRecorder

Starts the CNL recorder.

```cnl
StartCNLRecorder;
```

#### SetMachineExecutor

Sets the machine executor for distributed testing.

```cnl
SetMachineExecutor "MachineName";
```

---

## Product-Specific Commands

### Integration Server (IS)

#### LoginToIS

Logs into Integration Server.

```cnl
LoginToIS "Username" "Password";
```

#### ExpandAllNavigationLinksInIS

Expands all navigation links in IS.

```cnl
ExpandAllNavigationLinksInIS;
```

---

### My webMethods Server (MWS)

#### ExpandAllNavigationLinksInMws

Expands all navigation links in MWS.

```cnl
ExpandAllNavigationLinksInMws;
```

#### CloseAllTabsInMws

Closes all open tabs in MWS.

```cnl
CloseAllTabsInMws;
```

#### MWSTable

Interacts with MWS table elements.

```cnl
MWSTable "Action" on "TableElement";
```

---

### Command Central

#### ExpandAllInstancesInCC

Expands all instances in Command Central.

```cnl
ExpandAllInstancesInCC;
```

#### CommandCentralTable

Interacts with Command Central table elements.

```cnl
CommandCentralTable "Action" on "TableElement";
```

---

### CentraSite

#### ScrollDownAndClickInCentrasite

Scrolls down and clicks in CentraSite.

```cnl
ScrollDownAndClickInCentrasite "ElementId";
```

#### ScrollDownAndCheckInCentrasite

Scrolls down and checks in CentraSite.

```cnl
ScrollDownAndCheckInCentrasite "ElementId";
```

---

### Agile Apps

#### NavigateInAgileApps

Navigates within Agile Apps.

```cnl
NavigateInAgileApps "Path";
```

---

### Presto

#### NavigateInPresto

Navigates within Presto.

```cnl
NavigateInPresto "Path";
```

#### VerifySuccessOnMashupRun

Verifies success on mashup run in Presto.

```cnl
VerifySuccessOnMashupRun;
```

---

### OneData

#### NavigateInOneData

Navigates within OneData.

```cnl
NavigateInOneData "Path";
```

---

### Process Visibility

#### NavigateInProcessVisibility

Navigates within Process Visibility.

```cnl
NavigateInProcessVisibility "Path";
```

---

### SPM (Software AG Product Manager)

#### StartWebBrowserWithBasicAuth

Starts browser with basic authentication.

```cnl
StartWebBrowserWithBasicAuth "URL" "Username" "Password";
```

---

## Control Types Reference

### Supported Control Types

- **Button** - Clickable buttons
- **Link** - Hyperlinks (`<a>` tags)
- **TextField** - Single-line text input (`<input type="text">`)
- **TextArea** - Multi-line text input (`<textarea>`)
- **CheckBox** - Checkbox controls (`<input type="checkbox">`)
- **RadioButton** - Radio button controls (`<input type="radio">`)
- **ComboBox / Select** - Dropdown lists (`<select>`)
- **Label** - Text labels (`<label>`, `<span>`)
- **Image** - Image elements (`<img>`)
- **Div** - Division containers (`<div>`)
- **Video** - Video elements (`<video>`)
- **Tree** - Tree view controls
- **List** - List controls (`<ul>`, `<ol>`)
- **Table** - Table elements (`<table>`)
- **InputFile** - File upload controls (`<input type="file">`)
- **Option** - Option elements in select (`<option>`)

### Angular-Specific Control Types

- **md-select** - Angular Material select
- **md-option** - Angular Material option

---

## Relative Directions

### Supported Directions

- **following** - Element appears after the reference element in DOM
- **preceding** - Element appears before the reference element in DOM
- **below** - Element is positioned below the reference element visually
- **above** - Element is positioned above the reference element visually
- **beloworfollowing** - Element is below or after the reference element

---

## Command Modifiers

### Optional Modifiers (can be added to commands)

- **[doNotHandleAlert]** - Don't handle alerts during command execution
- **[doNotVerify]** - Skip verification after command execution
- **[doNotRetryOnError]** - Don't retry if command fails
- **[ignoreIsDisplayed]** - Ignore element visibility check
- **[ignoreJsScript]** - Don't use JavaScript for execution
- **[openInNewTab]** - Open URL in new tab (for StartWebBrowser/GoToURL)

### Example with Modifier

```cnl
Click "Button" Button [doNotHandleAlert];
Type "Text" in "Field" TextField [doNotVerify];
```

---

## Special Syntax Features

### Parameter Replacement

Use `${PropertyName}` to reference stored properties:

```cnl
Type "${username}" in "Username" TextField;
```

### Multiple Options (with #)

For MultiSelect and UnSelect:

```cnl
MultiSelect "Option1#Option2#Option3" in "ComboBox" ComboBox;
```

### Index-Based Selection

```cnl
Select "index=2" in "ComboBox" ComboBox;
```

### Value-Based Selection

```cnl
Select "value=optionValue" in "ComboBox" ComboBox;
```

---

## Command Execution Notes

1. **Element States**: Commands automatically wait for elements to be in the required state (VISIBLE, ENABLED, EDITABLE)
2. **Retry Mechanism**: Most commands have built-in retry logic for transient failures
3. **Frame Handling**: Commands automatically switch to the correct frame/iframe
4. **Alert Handling**: Alerts are handled automatically unless `[doNotHandleAlert]` is specified
5. **Verification**: Most commands perform automatic verification unless `[doNotVerify]` is specified
6. **Recovery**: Commands support recovery mechanisms for common failures

---

## Best Practices

1. **Use Stable Identifiers**: Prefer IDs over text when available
2. **Relative Positioning**: Use relative commands for dynamic elements
3. **Wait Strategies**: Let the tool handle waits; add explicit Sleep only when necessary
4. **Error Handling**: Use IfSatisfy for conditional logic
5. **Modifiers**: Use modifiers sparingly and only when needed
6. **Native Commands**: Use Native variants (ClickNative, TypeNative) when standard commands fail
7. **Product-Specific Commands**: Use product-specific commands for better reliability

---

## Total Command Count

**Generic Web Commands**: ~70 commands
**Product-Specific Commands**: ~15 commands
**Total**: ~85 commands

---

## Migration Checklist

When migrating to a new tool, ensure support for:

- [ ] All generic web commands
- [ ] Relative positioning (following, preceding, below, above)
- [ ] Element state handling (VISIBLE, ENABLED, EDITABLE)
- [ ] Command modifiers ([doNotHandleAlert], [doNotVerify], etc.)
- [ ] Parameter replacement (${PropertyName})
- [ ] Multi-option syntax (Option1#Option2)
- [ ] Index and value-based selection
- [ ] Frame/iframe handling
- [ ] Alert handling
- [ ] Retry mechanisms
- [ ] Product-specific commands (if applicable)
- [ ] Angular Material components (md-select, md-option)
- [ ] SVG element handling
- [ ] REST API testing
- [ ] File operations
- [ ] Screenshot and comparison features
