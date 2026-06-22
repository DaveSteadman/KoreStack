# KoreDeviceDriver

Purpose: To manage scripts and script based actions for the purposes of interacting with a local device. Both in reading state from a device, as well as initiating actions.

## Design

The system has two main sections, input and output. Input is used to read data from the device, while output is used to send commands to the device. The microservice is a single entity as the management of scripts and potentially damaging script actions is considered the services boundary.

Other systems will call for actions, or consume data items, but KoreDeviceScript is what facilitates it. 

Within this service scripts can be triggered (by a script itself) or on a schedule. 

## UI

We'll have a subsystem within KoreDevice, and the UI will be a list of skills, each with names, purpose (text field), stats about last run time.

Each skill has a run button

Clicking on a skill will open up a dedicated page to these skills, where we have a code-edit panel for the script itself. We can also see the history of this skill's execution meta data.

On the page itself, by default we have the chance to edit any field, with save and delete buttons. 
