# PycroFlow


>>> import PycroFlow.orchestration as por
>>> import PycroFlow.hamilton_architecture as ha
>>> ha.connect('18', 9600)
Open: COM18
>>> la = ha.LegacyArchitecture(ha.legacy_system_config, ha.legacy_tubing_config, '18', 9600)
>>> prot = {'flow_parameters': por.protocol['flow_parameters'], 'fluid': por.protocol_fluid}
>>> po = por.ProtocolOrchestrator(prot, fluid_system=la)
>>> po.start_orchestration()
>>> po.start_protocol()