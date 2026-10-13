# Protocol references and credit

Thank you to **Axel Koegler**, creator of **Projects @ Hillview**, for documenting local AlphaESS integration, dispatch behaviour and real-world firmware differences:

- https://projects.hillviewlodge.ie/alphaess/

This release is an original Python implementation informed by documented register facts and the architecture of Mark Graham's existing local controller. It does not redistribute Hillview YAML, dashboard cards, automations or images; their reuse licence has not been established. The integration's MIT licence applies only to original code and documentation in this repository.

Manufacturer primary protocol references, hosted by Hillview (copyright remains with their respective authors):

- AlphaESS Household Modbus Register Parameter List: https://projects.hillviewlodge.ie/_alphaess/AlphaESS-HouseholdModbusRegisterParameterList.pdf
- AlphaESS Household Register Map: https://projects.hillviewlodge.ie/_alphaess/AlphaESS-HouseholdModbusRegisterMapTable.pdf
- AlphaESS Modbus Dispatch Function: https://projects.hillviewlodge.ie/_alphaess/AlphaESS-ModbusDispatchFunction.pdf
- AlphaESS Modbus RTU/TCP handbook: https://projects.hillviewlodge.ie/_alphaess/AlphaESS-ModbusRTU%26TCP.pdf

These describe protocol facts, not a licence or a hardware support guarantee. Manufacturer dispatch guidance specifies 0.4 %/bit target scaling; Hillview documents a 0.392 variant. Manufacturer native reserve specifies 0.1 %/bit; the Hillview/current-controller variant uses 1 %/bit. Users must choose verified scales explicitly. Documentation reviewed 13 October 2026.

Dependency: [PyModbus](https://github.com/pymodbus-dev/pymodbus), separately BSD licensed. Home Assistant is separately Apache-2.0 licensed. No manufacturer endorsement is implied.
