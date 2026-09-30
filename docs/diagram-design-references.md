# 耦合图绘制参考

本软件的图形使用自己的几何图元和配色，没有复刻论文图片。设计决定参考以下公开原始研究的构型图：

- [Google Quantum AI, *Quantum error correction below the surface code threshold*, Nature (2025)](https://www.nature.com/articles/s41586-024-08449-y)：图 1 用明确的颜色区分数据比特与测量比特，参数统计与构型图分层呈现。本软件据此固定数据、X 测量和 Z 测量三类节点的基础颜色。
- [*Surface-code hardware Hamiltonian*, npj Quantum Information (2026)](https://www.nature.com/articles/s41534-026-01241-y)：图 1 明确绘出中心测量比特、周围数据比特与每一只专用可调耦合器，并区分直接残余耦合。本软件据此把比特—耦合器的两段物理连接建成独立对象，并以菱形表示耦合器、不同颜色区分直接与经耦合器连接。
- [*Demonstration of a quantum error detection code using a square lattice of four superconducting qubits*, Nature Communications (2015)](https://www.nature.com/articles/ncomms7979)：图 1 通过颜色区分码比特与 X/Z syndrome 比特，并单独绘出连接器。本软件据此保持角色图例与连接器图元分离。

这些是图示表达方式的参考，不表示本软件模型等同于文献中的器件电路或量子操作。
