async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        results = []
        
        for d in devices:
            # On récupère la liste des commandes supportées par l'appareil
            cmds = [c.command_name for c in d.definition.commands]
            
            # On ne traite que les appareils qui ont la commande setOperatingMode
            if "setOperatingMode" in cmds:
                try:
                    if target_mode == "ABSENCE":
                        # 1. On passe en mode 'away' (Absence)
                        await client.execute_command(d.device_url, "setOperatingMode", ["away"])
                        
                        # 2. Si l'appareil supporte le réglage de temp hors-gel, on force 10.0
                        if "setHolidaysTargetTemperature" in cmds:
                            await client.execute_command(d.device_url, "setHolidaysTargetTemperature", [10.0])
                        
                        results.append(f"✅ {d.label} : Mode Absence OK")
                    
                    else:
                        # Retour au planning interne (PROG / AUTO)
                        await client.execute_command(d.device_url, "setOperatingMode", ["internal"])
                        results.append(f"🏠 {d.label} : Mode Planning OK")
                
                except Exception as e:
                    results.append(f"❌ {d.label} : Erreur ({str(e)[:30]})")
        
        return "\n".join(results) if results else "Aucun radiateur compatible trouvé."
