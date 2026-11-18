# Manual técnico de Atlas — 4. Seguridad y gestión de credenciales

Este capítulo reúne las normas de seguridad que aplican a todo el código y la infraestructura de Atlas. Su cumplimiento es obligatorio y se revisa en la auditoría interna semestral, que se realiza en marzo y en septiembre.

## 4.1 Secretos y credenciales

Ningún secreto (contraseñas, claves de API, certificados, tokens) puede guardarse en el código fuente, en archivos de configuración versionados ni en las variables de un pull request. Todos los secretos se almacenan en HashiCorp Vault y los servicios los leen al iniciar mediante el sidecar de Vault en Kubernetes.

Si un secreto se sube por error a un repositorio, aunque sea a una rama que se borra enseguida, se considera comprometido. En ese caso hay que:

1. Revocar y rotar el secreto de inmediato, antes de cualquier otra acción.
2. Abrir un incidente de seguridad, que como mínimo se clasifica como SEV2.
3. Revisar los registros de acceso del sistema afectado desde el momento de la exposición.

Borrar el commit no es suficiente, porque el historial puede haber sido copiado o indexado.

## 4.2 Rotación de secretos

Los secretos tienen una vida máxima según su tipo:

- Contraseñas de bases de datos de producción: se rotan automáticamente cada 30 días.
- Claves de API de proveedores externos: cada 90 días.
- Certificados TLS internos: se emiten con una validez de 60 días y se renuevan automáticamente cuando les quedan 15 días.
- Tokens personales de acceso a los repositorios: caducan a los 30 días y no pueden renovarse; hay que generar uno nuevo.

## 4.3 Acceso a producción

Nadie tiene acceso permanente a producción. Para acceder a una base de datos o a un clúster de producción se pide un acceso temporal (just-in-time) desde el portal interno de accesos, indicando el motivo y el ticket asociado. El acceso de lectura se concede por un máximo de 4 horas y el acceso de escritura por un máximo de 1 hora; el acceso de escritura además requiere la aprobación de otra persona del equipo.

Todas las sesiones en producción quedan grabadas y se revisan por muestreo cada semana. Durante un incidente SEV1, la persona responsable del incidente puede conceder acceso de escritura sin la aprobación adicional, pero debe dejarlo registrado en el canal del incidente.

## 4.4 Autenticación del personal

Todas las cuentas del personal usan inicio de sesión único (SSO) con autenticación multifactor obligatoria. Solo se aceptan como segundo factor las llaves de seguridad físicas (FIDO2) o la aplicación autenticadora corporativa; los códigos por SMS no están permitidos.

Las contraseñas de las cuentas que no pueden usar SSO deben tener al menos 16 caracteres y se guardan en el gestor de contraseñas corporativo. Está prohibido compartir cuentas entre personas.

## 4.5 Seguridad de las APIs

La API pública de atlas-gateway se autentica con tokens OAuth 2.0 de corta duración (15 minutos). Los límites de tasa por defecto son de 100 peticiones por minuto por cliente, y los clientes corporativos pueden solicitar un límite mayor mediante un ticket al equipo de plataforma.

Los servicios internos se autentican entre sí con TLS mutuo (mTLS). Ningún servicio interno debe aceptar tráfico sin cifrar, ni siquiera dentro del clúster.

## 4.6 Datos personales

Atlas almacena datos personales de los destinatarios: nombre, dirección y teléfono. Estos datos:

- Nunca deben aparecer en los logs. Los logs solo pueden incluir el identificador del paquete, nunca el nombre ni el teléfono del destinatario.
- Se anonimizan antes de copiarse a staging o a dev.
- Se eliminan de los registros archivados después de 18 meses, junto con el archivo de los datos de seguimiento.

Cualquier solicitud de un cliente para acceder a sus datos o eliminarlos se atiende a través del equipo legal, que tiene un plazo de 10 días hábiles para responder.

## 4.7 Dependencias de terceros

Antes de añadir una dependencia nueva hay que comprobar que tenga una licencia compatible (MIT, Apache 2.0 o BSD) y que haya tenido actividad de mantenimiento en los últimos 12 meses. Las dependencias con licencia GPL requieren la aprobación del equipo legal.
