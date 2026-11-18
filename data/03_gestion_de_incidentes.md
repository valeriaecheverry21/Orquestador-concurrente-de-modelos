# Manual técnico de Atlas — 3. Gestión de incidentes

Este capítulo define cómo se clasifican los incidentes de Atlas, quién responde, en cuánto tiempo y qué se hace después de resolverlos.

## 3.1 Niveles de severidad

Todo incidente se clasifica en uno de cuatro niveles:

- **SEV1 (crítico)**: Atlas no está disponible para la mayoría de los usuarios, se están perdiendo eventos de escaneo o hay una brecha de seguridad confirmada. Ejemplo: atlas-gateway responde errores 5xx a más del 50 % de las peticiones.
- **SEV2 (alto)**: una funcionalidad importante está degradada para un grupo significativo de usuarios, pero existe una alternativa. Ejemplo: las notificaciones push no se envían, aunque el portal web muestra el estado correcto.
- **SEV3 (medio)**: un problema afecta a pocos usuarios o a una funcionalidad secundaria. Ejemplo: un reporte interno muestra datos con más de una hora de retraso.
- **SEV4 (bajo)**: un problema cosmético o sin impacto en los usuarios. Ejemplo: un error ortográfico en un mensaje del portal.

Ante la duda entre dos niveles, se elige el más grave. Siempre es posible bajar la severidad más adelante, pero subirla tarde retrasa la respuesta.

## 3.2 Guardias (on-call)

El equipo mantiene una rotación de guardia semanal que cambia todos los lunes a las 10:00. En cada turno hay una persona de guardia principal y una de respaldo. Las alertas se envían primero a la guardia principal; si no las reconoce a tiempo, se escalan automáticamente a la de respaldo y luego al líder técnico.

Los tiempos máximos para reconocer una alerta son:

| Severidad | Tiempo para reconocer | Tiempo para comunicar el primer estado |
|---|---|---|
| SEV1 | 5 minutos | 15 minutos |
| SEV2 | 15 minutos | 30 minutos |
| SEV3 | 4 horas hábiles | 1 día hábil |
| SEV4 | 2 días hábiles | No aplica |

Cada turno de guardia completo da derecho a un día libre compensatorio, que debe tomarse dentro de los 30 días siguientes. Si una persona recibe más de 2 alertas fuera del horario laboral en la misma noche, puede empezar a trabajar más tarde al día siguiente sin necesidad de pedir autorización.

## 3.3 Roles durante un incidente

En los incidentes SEV1 y SEV2 se asignan tres roles, que deben quedar escritos en el canal del incidente:

- **Responsable del incidente (incident commander)**: coordina la respuesta, decide las acciones y declara el incidente resuelto. No debe ponerse a depurar el problema; su trabajo es coordinar.
- **Responsable técnico**: investiga la causa y aplica las mitigaciones.
- **Responsable de comunicaciones**: publica actualizaciones del estado cada 30 minutos en el canal #atlas-incidentes y avisa al equipo de atención al cliente.

En SEV3 y SEV4 una sola persona puede asumir todos los roles.

## 3.4 Canal del incidente y registro

Por cada incidente SEV1 o SEV2 se crea un canal dedicado con el formato `#inc-AAAAMMDD-descripcion-corta`. Todo lo que se decide se registra en ese canal con la hora, para poder reconstruir la cronología después. Las conversaciones por mensajes privados o llamadas no registradas deben resumirse en el canal.

## 3.5 Mitigar antes que diagnosticar

La prioridad durante un incidente es restaurar el servicio, no encontrar la causa raíz. Las mitigaciones preferidas, en orden, son: revertir el último despliegue, desactivar la funcionalidad afectada con su feature flag, escalar horizontalmente el servicio y, como último recurso, redirigir el tráfico a la otra zona de disponibilidad.

## 3.6 Postmortems

Todo incidente SEV1 o SEV2 requiere un postmortem escrito, que debe publicarse en un plazo máximo de 5 días hábiles después de resolverse el incidente. Para los SEV3 el postmortem es opcional y lo decide el líder técnico.

Los postmortems son sin culpables (blameless): describen qué falló en los sistemas y en los procesos, no quién se equivocó. Cada postmortem debe incluir la cronología, el impacto en usuarios, la causa raíz, lo que funcionó bien, lo que funcionó mal y una lista de acciones correctivas, cada una con una persona responsable y una fecha comprometida.

Las acciones correctivas de un SEV1 tienen prioridad sobre cualquier funcionalidad nueva del trimestre. El postmortem se revisa en la reunión mensual de fiabilidad, que se realiza el último miércoles de cada mes.
